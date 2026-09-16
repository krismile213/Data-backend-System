import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

/**
 * EChart — ECharts 通用包装组件
 * props: option (ECharts 配置), height (px, 默认 360)
 */
export default function EChart({ option, height = 360 }) {
  const ref = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!ref.current) return undefined;
    chartRef.current = echarts.init(ref.current);
    const onResize = () => chartRef.current && chartRef.current.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      if (chartRef.current) {
        chartRef.current.dispose();
        chartRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (chartRef.current && option) {
      chartRef.current.setOption(option, true);
      chartRef.current.resize();
    }
  }, [option]);

  return <div ref={ref} style={{ width: '100%', height }} />;
}
