import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * useApi —— 统一的数据请求 hook
 *
 * 改造前：每个页面都手写一遍
 *   const [data, setData] = useState(null);
 *   const [error, setError] = useState('');
 *   useEffect(() => { fetchX().then(setData).catch(e => setError(e.message)); }, [dep]);
 * 这段三件套在 17 个页面里重复，且**全都缺少竞态防护**：
 * 快速切换批次时，先发出的旧请求可能后返回，把新批次的数据覆盖掉（页面显示上一个批次的数据）。
 *
 * 用法：
 *   const { data, error, loading, refetch } = useApi(() => fetchScenarios(), []);
 *   const { data } = useApi(() => fetchOverview(batch), [batch], { enabled: !!batch });
 *
 * 参数：
 *   fetcher   () => Promise，注意它只在 deps 变化时重新执行
 *   deps      依赖数组，任一变化则自动重取
 *   enabled   为 false 时不请求（如 batch 还没选中）；默认 true
 *   onSuccess 成功回调（用于派生子状态）
 *
 * 返回：
 *   data      结果；初始为 null
 *   error     错误文案（字符串），无错误为空串
 *   loading   是否请求中
 *   refetch   手动重取（不抛异常，错误进 error）
 *   setData   本地改写（乐观更新用）
 *   setError
 */
export function useApi(fetcher, deps = [], opts = {}) {
  const { enabled = true, initialData = null, onSuccess } = opts;

  const [data, setData] = useState(initialData);
  const [error, setError] = useState('');
  // 初始即视为「正在加载」：否则首帧 loading=false 且 data=null，
  // 页面里 `if (loading) ... ; if (!data) return null;` 的组合会先渲染出一帧空白。
  const [loading, setLoading] = useState(enabled);

  // 请求序号：只有最新一次请求的结果才允许落地，避免乱序覆盖
  const seq = useRef(0);

  // 用 ref 持有 fetcher / onSuccess，这样调用方可以写内联箭头函数而不触发重复请求
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;

  const run = useCallback(async () => {
    if (!enabled) return null;
    const my = ++seq.current;
    setLoading(true);
    setError('');
    try {
      const result = await fetcherRef.current();
      if (my !== seq.current) return null; // 已有更新的请求发出，丢弃本次结果
      setData(result);
      if (onSuccessRef.current) onSuccessRef.current(result);
      return result;
    } catch (err) {
      if (my === seq.current) setError(err?.message || String(err) || '加载失败');
      return null;
    } finally {
      if (my === seq.current) setLoading(false);
    }
    // deps 由调用方保证长度稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  useEffect(() => {
    if (enabled) run();
  }, [run]);

  return { data, error, loading, refetch: run, setData, setError };
}

export default useApi;
