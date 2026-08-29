import { useEffect, useState } from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';
import { Activity, LayoutDashboard, List, Cpu } from 'lucide-react';
import { getHealth } from '../../api/client';
import type { HealthResponse } from '../../api/types';
import clsx from 'clsx';

export const Layout = () => {
  const location = useLocation();
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center">
              <Activity className="h-6 w-6 text-blue-600 mr-2" />
              <div className="flex flex-col">
                <span className="text-lg font-semibold tracking-tight text-slate-900 leading-tight">RecoverOS</span>
                <span className="text-[10px] text-slate-500 font-medium uppercase tracking-wider leading-none">Payment Recovery Intelligence</span>
              </div>
            </div>
            <nav className="flex space-x-8">
              <Link
                to="/"
                className={clsx(
                  "inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium",
                  location.pathname === '/' 
                    ? "border-blue-500 text-slate-900" 
                    : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700"
                )}
              >
                <LayoutDashboard className="w-4 h-4 mr-2" />
                Command Center
              </Link>
              <Link
                to="/journeys"
                className={clsx(
                  "inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium",
                  location.pathname.startsWith('/journeys') 
                    ? "border-blue-500 text-slate-900" 
                    : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700"
                )}
              >
                <List className="w-4 h-4 mr-2" />
                Journeys
              </Link>
            </nav>
            <div className="flex items-center space-x-2">
              <span className="hidden sm:inline-flex items-center rounded-md bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-600/20">
                <Cpu className="w-3 h-3 mr-1 text-blue-600" />
                {health?.model_version || 'diagnosis_erv_v2'} (Advisory)
              </span>
              <span className={clsx(
                "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
                health?.status === 'healthy' 
                  ? "bg-green-50 text-green-700 ring-green-600/20" 
                  : "bg-amber-50 text-amber-700 ring-amber-600/20"
              )}>
                <span className={clsx(
                  "w-1.5 h-1.5 rounded-full mr-1.5",
                  health?.status === 'healthy' ? "bg-green-500" : "bg-amber-500"
                )}></span>
                {health?.status === 'healthy' ? 'System Online' : 'Connecting...'}
              </span>
              <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-400/20">
                Razorpay Sandbox
              </span>
            </div>
          </div>
        </div>
      </header>

      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet />
      </main>
    </div>
  );
};
