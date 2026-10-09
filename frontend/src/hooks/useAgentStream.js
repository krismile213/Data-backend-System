/**
 * useAgentStream —— 助手事件流（SSE）连接 hook
 *
 * 为什么**手写 `fetch` + `ReadableStream`** 而不用 `EventSource`：
 *   1) `EventSource` **不能带自定义请求头** —— 我们需要带 `Last-Event-ID` 支持断线续传
 *      （Agent `server.py:835` 靠这个头重放）；
 *   2) `EventSource` 只能 GET，无法满足将来带 body 的场景；
 *   3) `EventSource` 没有 `AbortController`，关闭/切换会话时无法精确中止。
 *
 * 帧格式（与 Agent `server.py:841-842` 一致）：`id: <seq>\ndata: <json>\n\n`。
 * 注意 `hello` 帧**无 id**（`server.py:846-848`）——无 id 的帧不更新 lastEventId。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { agentEventsUrl } from '../api.js';
import { createSseParser } from './sseParser.js';

/**
 * @param {object}   opts
 * @param {string}   opts.session  会话 id
 * @param {Function} opts.onEvent  收到一个事件（已 JSON.parse 的对象）时回调
 * @param {Function} opts.onError  连接/读流出错时回调
 * @returns {{ start: Function, stop: Function, connected: boolean }}
 */
export function useAgentStream({ session, onEvent, onError }) {
  const abortRef = useRef(null);
  const lastIdRef = useRef(null);   // 最近一个 id，重连时作 Last-Event-ID
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const [connected, setConnected] = useState(false);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setConnected(false);
  }, []);

  const start = useCallback(() => {
    if (abortRef.current) return; // 已在连接中，避免重复开流
    const controller = new AbortController();
    abortRef.current = controller;

    (async () => {
      const headers = {};
      // 断线重连：带上最近的 id，让上游从断点重放（server.py:835-852）
      if (lastIdRef.current) headers['Last-Event-ID'] = lastIdRef.current;

      let res;
      try {
        res = await fetch(agentEventsUrl(session), {
          headers,
          signal: controller.signal,
        });
      } catch (err) {
        if (err && err.name !== 'AbortError') onErrorRef.current?.(err);
        if (abortRef.current === controller) abortRef.current = null;
        return;
      }

      if (!res.ok || !res.body) {
        onErrorRef.current?.(new Error(`助手事件流连接失败（HTTP ${res.status}）`));
        if (abortRef.current === controller) abortRef.current = null;
        return;
      }

      setConnected(true);
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      const parser = createSseParser();

      // 一个解析出的帧 → 交给上层：先记 id（供重连），再 JSON.parse 派发
      const dispatch = (frame) => {
        if (frame.id) lastIdRef.current = frame.id;
        let payload;
        try {
          payload = JSON.parse(frame.data);
        } catch (e) {
          payload = { kind: 'raw', text: frame.data };
        }
        onEventRef.current?.(payload);
      };

      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          const text = decoder.decode(value, { stream: true });
          for (const frame of parser.push(text)) dispatch(frame);
        }
        // 流正常结束：把残留缓冲当最后一帧（无结尾空行时）
        for (const frame of parser.flush()) dispatch(frame);
      } catch (err) {
        if (err && err.name !== 'AbortError') onErrorRef.current?.(err);
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setConnected(false);
      }
    })();
  }, [session]);

  // 卸载时确保中止，避免流泄漏
  useEffect(() => () => stop(), [stop]);

  return { start, stop, connected };
}

export default useAgentStream;
