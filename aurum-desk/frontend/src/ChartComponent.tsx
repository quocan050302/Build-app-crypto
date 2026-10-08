import { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import axios from 'axios';

interface CandleData {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface ChartComponentProps {
  symbol: string;
  timeframe: string;
}

export function ChartComponent({ symbol, timeframe }: ChartComponentProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Create chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#1a1a1a' },
        textColor: '#d1d5db',
      },
      grid: {
        vertLines: { color: '#2d2d2d' },
        horzLines: { color: '#2d2d2d' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 600,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      }
    });

    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        // Sync new data first
        await axios.post(`http://127.0.0.1:8000/api/v1/candles/sync?symbol=${symbol}&timeframe=${timeframe}&limit=200`);
        
        // Fetch from local DB
        const res = await axios.get(`http://127.0.0.1:8000/api/v1/candles/${symbol}/${timeframe}?limit=200`);
        
        if (res.data && Array.isArray(res.data) && seriesRef.current) {
          const formattedData: CandleData[] = res.data.map((c: any) => ({
            time: (c.timestamp / 1000) as Time, // Lightweight charts uses seconds for timestamp
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close
          })).sort((a, b) => (a.time as number) - (b.time as number));

          seriesRef.current.setData(formattedData);
        }
      } catch (err) {
        console.error("Error fetching chart data", err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [symbol, timeframe]);

  return (
    <div className="relative w-full h-[600px] border border-charcoal-700 rounded overflow-hidden">
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-charcoal-900 bg-opacity-70 text-aurum-500">
          Loading Data...
        </div>
      )}
      <div ref={chartContainerRef} className="w-full h-full" />
    </div>
  );
}
