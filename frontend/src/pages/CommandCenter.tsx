import { useEffect, useState } from 'react';
import { 
  AlertCircle,
  ArrowRight,
  Brain,
  CheckCircle2,
  CircleDollarSign,
  ShieldCheck,
  Zap,
  Activity,
  CreditCard,
  RefreshCcw,
  Bot
} from 'lucide-react';
import { getOverview } from '../api/client';
import type { DashboardOverviewResponse } from '../api/types';

const MetricCard = ({ title, value, subtitle, icon: Icon, colorClass = "text-blue-600" }: { title: string, value: string, subtitle?: string, icon: any, colorClass?: string }) => (
  <div className="bg-white rounded-xl shadow-card p-6 border border-slate-100 flex flex-col justify-between h-full">
    <div className="flex justify-between items-start">
      <div className="flex-1">
        <p className="text-sm font-medium text-slate-500 mb-1">{title}</p>
        <h3 className="text-3xl font-bold text-slate-900">{value}</h3>
      </div>
      <div className={`p-3 rounded-lg bg-slate-50 ${colorClass}`}>
        <Icon className="w-6 h-6" />
      </div>
    </div>
    {subtitle && <p className="text-sm text-slate-500 mt-4 font-medium">{subtitle}</p>}
  </div>
);

const FlowNode = ({ icon: Icon, title, description, highlight = false }: any) => (
  <div className="flex flex-col items-center text-center max-w-[140px]">
    <div className={`w-14 h-14 rounded-2xl flex items-center justify-center mb-3 shadow-sm
      ${highlight ? 'bg-blue-600 text-white shadow-blue-500/20' : 'bg-white text-slate-700 border border-slate-200'}`}>
      <Icon className="w-6 h-6" />
    </div>
    <h4 className={`text-sm font-semibold mb-1 ${highlight ? 'text-blue-700' : 'text-slate-800'}`}>{title}</h4>
    <p className="text-[11px] text-slate-500 leading-tight">{description}</p>
  </div>
);

const FlowArrow = () => (
  <div className="flex items-center px-2 text-slate-300">
    <ArrowRight className="w-5 h-5" />
  </div>
);

export default function CommandCenter() {
  const [data, setData] = useState<DashboardOverviewResponse | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    getOverview()
      .then(setData)
      .catch((err) => {
        console.error(err);
        setError(true);
      });
  }, []);

  if (error) {
    return (
      <div className="bg-red-50 text-red-800 p-6 rounded-xl border border-red-200 flex items-center">
        <AlertCircle className="w-6 h-6 mr-3 text-red-600" />
        <div>
          <h3 className="font-semibold">Backend Connection Failed</h3>
          <p className="text-sm mt-1">Make sure the RecoverOS FastAPI server is running on port 8000.</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="animate-pulse space-y-8">
        <div className="h-32 bg-slate-200 rounded-xl w-full"></div>
        <div className="h-64 bg-slate-200 rounded-xl w-full"></div>
      </div>
    );
  }

  const formatCurrency = (val: number) => `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const formatPercent = (val: number) => `${(val * 100).toFixed(1)}%`;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Command Center</h1>
        <p className="text-slate-500 mt-1">High-level recovery operations and economic performance overview.</p>
      </div>

      {/* Primary KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <MetricCard
          title="Revenue at Risk"
          value={formatCurrency(data.revenue_at_risk)}
          subtitle={`${data.active_journeys + data.recovered_journeys + data.escalated_journeys + data.exhausted_journeys} Total Journeys`}
          icon={AlertCircle}
          colorClass="text-orange-500"
        />
        <MetricCard
          title="Recovered Value"
          value={formatCurrency(data.recovered_amount)}
          subtitle={data.recovery_rate > 0 ? `${formatPercent(data.recovery_rate)} of at-risk revenue` : "No recoveries yet"}
          icon={CheckCircle2}
          colorClass="text-green-500"
        />
        <MetricCard
          title="Recovery Cost"
          value={formatCurrency(data.recovery_cost)}
          subtitle="Direct API & action execution costs"
          icon={CircleDollarSign}
          colorClass="text-rose-500"
        />
        <MetricCard
          title="Net Recovered Value"
          value={formatCurrency(data.net_recovered_value)}
          subtitle="Recovered Value - Recovery Cost"
          icon={Activity}
          colorClass="text-blue-600"
        />
      </div>

      {/* Secondary Operational Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 bg-white p-6 rounded-xl border border-slate-100 shadow-sm">
        <div className="border-r border-slate-100 px-4 last:border-0">
          <p className="text-xs text-slate-500 uppercase font-semibold tracking-wider mb-1">Active</p>
          <p className="text-xl font-bold text-slate-800">{data.active_journeys}</p>
        </div>
        <div className="border-r border-slate-100 px-4 last:border-0">
          <p className="text-xs text-slate-500 uppercase font-semibold tracking-wider mb-1">Escalated</p>
          <p className="text-xl font-bold text-slate-800">{data.escalated_journeys}</p>
        </div>
        <div className="border-r border-slate-100 px-4 last:border-0">
          <p className="text-xs text-slate-500 uppercase font-semibold tracking-wider mb-1">Exhausted</p>
          <p className="text-xl font-bold text-slate-800">{data.exhausted_journeys}</p>
        </div>
        <div className="px-4">
          <p className="text-xs text-slate-500 uppercase font-semibold tracking-wider mb-1">Execution Unknown</p>
          <p className="text-xl font-bold text-amber-600 flex items-center">
            {data.execution_unknown_count}
            {data.execution_unknown_count > 0 && <AlertCircle className="w-4 h-4 ml-2" />}
          </p>
        </div>
      </div>

      {/* How RecoverOS Thinks Visual */}
      <div className="bg-white rounded-xl shadow-card border border-slate-100 p-8 overflow-hidden">
        <div className="mb-8">
          <h2 className="text-lg font-bold text-slate-900 flex items-center">
            <Brain className="w-5 h-5 mr-2 text-blue-600" />
            How RecoverOS Recovers Revenue
          </h2>
          <p className="text-sm text-slate-500 mt-1">The closed-loop architecture executed for every failed payment.</p>
        </div>

        <div className="flex items-center justify-between overflow-x-auto pb-4 hide-scrollbar">
          <FlowNode 
            icon={AlertCircle} 
            title="Payment Failure" 
            description="Webhook ingests failed transaction & initializes state" 
          />
          <FlowArrow />
          
          <FlowNode 
            icon={Bot} 
            title="AI Diagnosis" 
            description="Analyzes failure root cause & estimates action probabilities"
            highlight={true}
          />
          <FlowArrow />
          
          <FlowNode 
            icon={CircleDollarSign} 
            title="Expected Value" 
            description="Calculates ERV mapping probability against cost & friction" 
            highlight={true}
          />
          <FlowArrow />
          
          <FlowNode 
            icon={ShieldCheck} 
            title="Guardrails" 
            description="Filters unsafe/terminal actions deterministically" 
          />
          <FlowArrow />
          
          <FlowNode 
            icon={Zap} 
            title="Bounded Action" 
            description="Selects and executes the highest-yield permitted action" 
          />
          <FlowArrow />
          
          <FlowNode 
            icon={CreditCard} 
            title="Razorpay" 
            description="Interacts with real Razorpay APIs (Payment Links, Retries)" 
          />
          <FlowArrow />
          
          <FlowNode 
            icon={RefreshCcw} 
            title="Reconciliation" 
            description="Closes loop on successful settlement & protects against double-charge" 
          />
        </div>
      </div>

      {/* Decision Intelligence & ERV Economics Overview */}
      <div className="bg-white rounded-xl shadow-card border border-slate-100 p-6">
        <div className="flex items-center justify-between pb-4 mb-4 border-b border-slate-100">
          <div className="flex items-center">
            <div className="p-2 bg-blue-50 text-blue-600 rounded-lg mr-3">
              <CircleDollarSign className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-900 uppercase tracking-wider">Decision Intelligence & ERV Principles</h2>
              <p className="text-xs text-slate-500 mt-0.5">Mathematical foundation governing automated recovery interventions</p>
            </div>
          </div>
          <span className="inline-flex items-center rounded-md bg-blue-50 px-2.5 py-1 text-xs font-bold text-blue-700 ring-1 ring-inset ring-blue-600/20">
            DETERMINISTIC OPTIMIZER
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Card 1 */}
          <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
            <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5 flex items-center">
              <span className="w-2 h-2 bg-blue-600 rounded-full mr-2"></span>
              1. The ERV Optimization Formula
            </h3>
            <p className="text-xs font-mono bg-white p-2 rounded border border-slate-200 text-slate-800 font-semibold mb-2">
              ERV = (P_pred × Amount) − Cost − Friction
            </p>
            <p className="text-xs text-slate-600 leading-relaxed">
              Every candidate action is priced in INR (₹). Actions with negative Expected Recovery Value or excessive customer friction penalty are automatically suppressed.
            </p>
          </div>

          {/* Card 2 */}
          <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
            <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5 flex items-center">
              <span className="w-2 h-2 bg-emerald-600 rounded-full mr-2"></span>
              2. Zero Execution Authority AI
            </h3>
            <p className="text-xs font-mono bg-white p-2 rounded border border-slate-200 text-slate-800 font-semibold mb-2">
              Role: Semantic Root-Cause Diagnostic
            </p>
            <p className="text-xs text-slate-600 leading-relaxed">
              The LLM acts strictly as an unprivileged diagnostic advisor emitting probability vectors. It has zero API keys to move money and cannot bypass deterministic guardrails.
            </p>
          </div>

          {/* Card 3 */}
          <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
            <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5 flex items-center">
              <span className="w-2 h-2 bg-purple-600 rounded-full mr-2"></span>
              3. Counterfactual Advantage
            </h3>
            <p className="text-xs font-mono bg-white p-2 rounded border border-slate-200 text-slate-800 font-semibold mb-2">
              Advantage = ERV_selected − ERV_fallback
            </p>
            <p className="text-xs text-slate-600 leading-relaxed">
              Every decision mathematically proves its value by computing the exact rupee delta generated compared to the next-best allowed alternative action.
            </p>
          </div>
        </div>
      </div>

    </div>
  );
}
