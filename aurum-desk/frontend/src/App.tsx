import { useState } from 'react';
import { ChartComponent } from './ChartComponent';

function App() {
  const [timeframe, setTimeframe] = useState('15M');
  const timeframes = ['1M', '5M', '15M', '1H', '4H', 'D'];

  return (
    <div className="min-h-screen bg-charcoal-900 text-gray-200 flex flex-col">
      <header className="bg-charcoal-800 border-b border-charcoal-700 p-4 flex justify-between items-center">
        <h1 className="text-xl font-bold text-aurum-500 tracking-wider">AURUM DESK <span className="text-xs text-gray-400 font-normal">| PAPER TRADING</span></h1>
        
        <div className="flex bg-charcoal-900 p-1 rounded">
          {timeframes.map(tf => (
            <button 
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-3 py-1 rounded text-sm ${timeframe === tf ? 'bg-charcoal-700 text-aurum-500' : 'text-gray-400 hover:text-gray-200'}`}
            >
              {tf}
            </button>
          ))}
        </div>
      </header>
      
      <main className="flex-1 p-4 grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Main Chart Area */}
        <div className="lg:col-span-3 flex flex-col gap-4">
          <div className="bg-charcoal-800 rounded shadow-lg p-4 flex-1">
            <h2 className="mb-4 text-lg text-gray-300">XAUUSDT - Bitget - {timeframe}</h2>
            <ChartComponent symbol="XAUUSDT" timeframe={timeframe} />
          </div>
        </div>

        {/* Side Panel for Signals & Info */}
        <div className="bg-charcoal-800 rounded shadow-lg p-4 flex flex-col gap-4">
          <div className="border-b border-charcoal-700 pb-2">
            <h3 className="text-aurum-500 font-medium">Trạng Thái Hệ Thống</h3>
            <div className="mt-2 text-sm">
              <p className="flex justify-between"><span className="text-gray-400">Data Feed:</span> <span className="text-green-500">Connected</span></p>
              <p className="flex justify-between"><span className="text-gray-400">Engine:</span> <span className="text-green-500">Active</span></p>
            </div>
          </div>
          
          <div className="border-b border-charcoal-700 pb-2 flex-1">
            <h3 className="text-aurum-500 font-medium mb-2">Tín Hiệu (Paper Trading)</h3>
            <div className="bg-charcoal-900 p-3 rounded text-sm text-gray-400 text-center italic">
              Đang phân tích cấu trúc...
            </div>
          </div>
          
          <div>
            <h3 className="text-aurum-500 font-medium mb-2">Quản trị vốn (Giả lập)</h3>
            <div className="text-sm">
              <p className="flex justify-between"><span className="text-gray-400">Vốn:</span> <span>$1,000.00</span></p>
              <p className="flex justify-between"><span className="text-gray-400">Rủi ro/lệnh:</span> <span>0.5% ($5.00)</span></p>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
