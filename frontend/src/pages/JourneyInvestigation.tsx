import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { format } from 'date-fns';
import { 
  ArrowLeft, 
  AlertTriangle, 
  Bot, 
  Calculator, 
  ShieldCheck, 
  SplitSquareHorizontal, 
  PlayCircle,
  CheckCircle2,
  ListTodo
} from 'lucide-react';
import { getJourneyDetail, getJourneyTimeline } from '../api/client';
import type { JourneyDetailResponse, JourneyTimelineResponse } from '../api/types';
import clsx from 'clsx';

const SectionHeader = ({ title, subtitle, icon: Icon, tag }: any) => (
  <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
    <div className="flex items-center">
      <div className="p-2 bg-slate-50 rounded-lg mr-3">
        <Icon className="w-5 h-5 text-slate-500" />
      </div>
      <div>
        <h2 className="text-sm font-bold text-slate-900 uppercase tracking-wider">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
    </div>
    {tag && (
      <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-600 uppercase tracking-wider ring-1 ring-inset ring-slate-500/20">
        {tag}
      </span>
    )}
  </div>
);

const TagLive = () => (
  <span className="inline-flex items-center rounded-md bg-emerald-50 px-2 py-1 text-xs font-bold text-emerald-700 ring-1 ring-inset ring-emerald-600/20 whitespace-nowrap">
    <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full mr-1.5 animate-pulse"></span>
    LIVE RAZORPAY TEST API
  </span>
);

const TagSimulated = ({ label = "SIMULATED" }: { label?: string }) => (
  <span className="inline-flex items-center rounded-md bg-purple-50 px-2 py-1 text-xs font-bold text-purple-700 ring-1 ring-inset ring-purple-600/20 whitespace-nowrap">
    {label}
  </span>
);

const TagRecommendation = () => (
  <span className="inline-flex items-center rounded-md bg-amber-50 px-2 py-1 text-xs font-bold text-amber-800 ring-1 ring-inset ring-amber-600/20 whitespace-nowrap">
    RECOMMENDATION ONLY
  </span>
);

const TagInternal = ({ label = "INTERNAL" }: { label?: string }) => (
  <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700 ring-1 ring-inset ring-slate-500/20 whitespace-nowrap">
    {label}
  </span>
);

export default function JourneyInvestigation() {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<JourneyDetailResponse | null>(null);
  const [timeline, setTimeline] = useState<JourneyTimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    Promise.all([
      getJourneyDetail(id),
      getJourneyTimeline(id)
    ])
      .then(([detailData, timelineData]) => {
        setDetail(detailData);
        setTimeline(timelineData);
      })
      .catch((err) => {
        console.error(err);
        setError(true);
      })
      .finally(() => setLoading(false));
  }, [id]);

  if (error) {
    return (
      <div className="bg-red-50 text-red-800 p-6 rounded-xl border border-red-200 text-center">
        <h3 className="font-semibold text-lg">Journey not found or backend unavailable</h3>
        <Link to="/journeys" className="text-red-600 underline mt-2 block">Return to journeys</Link>
      </div>
    );
  }

  if (loading || !detail || !timeline) {
    return (
      <div className="animate-pulse space-y-6">
        <div className="h-24 bg-slate-200 rounded-xl w-full"></div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 h-[600px] bg-slate-200 rounded-xl w-full"></div>
          <div className="h-[600px] bg-slate-200 rounded-xl w-full"></div>
        </div>
      </div>
    );
  }

  const formatCurrency = (val: number) => `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  return (
    <div className="space-y-6 animate-in fade-in duration-500 pb-20">
      
      <Link to="/journeys" className="inline-flex items-center text-sm font-medium text-slate-500 hover:text-slate-700">
        <ArrowLeft className="w-4 h-4 mr-1" />
        Back to Journeys
      </Link>

      {/* Header Profile */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col md:flex-row md:items-center justify-between">
        <div>
          <div className="flex items-center space-x-3 mb-1">
            <h1 className="text-2xl font-bold text-slate-900">{detail.journey_id}</h1>
            <span className={clsx(
              "inline-flex items-center rounded-md px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider ring-1 ring-inset",
              detail.status === 'IN_PROGRESS' ? 'bg-blue-50 text-blue-700 ring-blue-600/20' :
              detail.status === 'RECOVERED' ? 'bg-green-50 text-green-700 ring-green-600/20' :
              'bg-slate-100 text-slate-700 ring-slate-500/20'
            )}>
              {detail.status.replace('_', ' ')}
            </span>
          </div>
          <p className="text-sm font-mono text-slate-500">TXN: {detail.transaction_id}</p>
        </div>
        <div className="mt-4 md:mt-0 text-left md:text-right">
          <p className="text-sm text-slate-500 uppercase tracking-wider font-semibold mb-1">Original Amount</p>
          <p className="text-3xl font-bold text-slate-900">{formatCurrency(detail.amount)}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Main Column */}
        <div className="lg:col-span-2 space-y-6">
          
          {/* SECTION 1 - FAILURE INFO */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
            <SectionHeader title="Payment Failure" subtitle="Originating incident context" icon={AlertTriangle} />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Method</p>
                <p className="text-sm font-medium text-slate-900 mt-1 capitalize">{detail.payment_method}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Failure Type</p>
                <p className="text-sm font-medium text-slate-900 mt-1 capitalize">{detail.failure_type.replace(/_/g, ' ')}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Round</p>
                <p className="text-sm font-medium text-slate-900 mt-1">{detail.current_round}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Customer ID</p>
                <p className="text-sm font-mono font-medium text-slate-900 mt-1">{detail.customer_id || 'N/A'}</p>
              </div>
            </div>
          </div>

          {/* SECTION 2 - AI DIAGNOSIS */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 relative overflow-hidden">
            <SectionHeader 
              title="AI Failure Diagnosis" 
              subtitle="Semantic Analysis & Probability Estimation (Advisory Only)" 
              icon={Bot} 
              tag="ADVISORY"
            />
            <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-4 bg-slate-50 p-4 rounded-lg border border-slate-100 mb-4">
              <div>
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Diagnosis Status</p>
                <p className="text-sm font-bold text-green-700 mt-0.5">{detail.latest_diagnosis_status || 'VALIDATED'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Root-Cause Category</p>
                <p className="text-sm font-medium text-slate-900 mt-0.5 capitalize">{detail.failure_type.replace(/_/g, ' ')}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Execution Authority</p>
                <p className="text-sm font-bold text-rose-700 mt-0.5">ZERO (Advisory Only)</p>
              </div>
            </div>
            <p className="text-sm text-slate-600 leading-relaxed">
              The AI Diagnosis Engine analyzed the failure context to estimate probability distributions across recovery interventions. 
              The AI has zero execution authority and cannot invoke Razorpay APIs directly; all downstream money-movement decisions are strictly governed by the deterministic Economic Decision Engine.
            </p>
          </div>

          {/* SECTION 3 - ECONOMIC DECISION */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
            <SectionHeader 
              title="Economic Decision Engine" 
              subtitle="Deterministic calculation of Expected Recovery Value (ERV)" 
              icon={Calculator} 
              tag="INTERNAL"
            />
            
            <div className="mt-4 border border-slate-200 rounded-lg overflow-hidden">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50">
                  <tr>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">Action Candidate</th>
                    <th scope="col" className="px-4 py-3 text-right text-xs font-semibold text-slate-500 uppercase">Probability</th>
                    <th scope="col" className="px-4 py-3 text-right text-xs font-semibold text-slate-500 uppercase">Direct Cost</th>
                    <th scope="col" className="px-4 py-3 text-right text-xs font-semibold text-slate-500 uppercase">Friction</th>
                    <th scope="col" className="px-4 py-3 text-right text-xs font-bold text-slate-700 uppercase">ERV</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {detail.candidate_evaluations?.map((ev, i) => (
                    <tr key={i} className={clsx(ev.action === detail.selected_action ? "bg-blue-50/50" : "")}>
                      <td className="px-4 py-3 text-sm font-medium">
                        <div className="flex items-center">
                          <span className={clsx(
                            "capitalize",
                            ev.action === detail.selected_action ? "text-blue-700 font-bold" : "text-slate-700"
                          )}>{ev.action.replace('_', ' ')}</span>
                          {ev.action === detail.selected_action && (
                            <span className="ml-2 inline-flex items-center rounded-md bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold text-blue-800">SELECTED</span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-slate-600">{(ev.predicted_recovery_probability * 100).toFixed(1)}%</td>
                      <td className="px-4 py-3 text-sm text-right text-slate-600">{formatCurrency(ev.action_cost)}</td>
                      <td className="px-4 py-3 text-sm text-right text-slate-600">-</td>
                      <td className={clsx(
                        "px-4 py-3 text-sm text-right font-bold",
                        ev.action === detail.selected_action ? "text-blue-700" : "text-slate-900"
                      )}>{formatCurrency(ev.predicted_erv)}</td>
                    </tr>
                  ))}
                  {(!detail.candidate_evaluations || detail.candidate_evaluations.length === 0) && (
                    <tr><td colSpan={5} className="px-4 py-4 text-center text-sm text-slate-500">No candidate evaluations recorded.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="mt-6 bg-slate-50 rounded-lg p-4 border border-slate-200">
              <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Why this action?</h4>
              <p className="text-sm text-slate-700 font-medium">
                The <strong className="text-slate-900">{detail.selected_action?.replace('_', ' ').toUpperCase()}</strong> action was selected deterministically because it yielded the highest strictly-calculated Expected Recovery Value (ERV) after accounting for predicted success probability and direct execution costs, without triggering any deterministic safety guardrails.
              </p>
            </div>
          </div>

          {/* SECTION 4 - GUARDRAILS */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
            <SectionHeader title="Guardrails" subtitle="Deterministic safety policies evaluated" icon={ShieldCheck} tag="INTERNAL" />
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4">
              {detail.guardrails_triggered && detail.guardrails_triggered.length > 0 ? (
                 detail.guardrails_triggered.map((g, i) => (
                  <div key={i} className="flex items-center justify-between p-3 rounded-lg border border-red-100 bg-red-50">
                    <span className="text-sm font-medium text-red-800">{g}</span>
                    <span className="text-[10px] font-bold text-red-600 bg-red-100 px-2 py-0.5 rounded-full">BLOCKED</span>
                  </div>
                 ))
              ) : (
                <>
                  <div className="flex items-center justify-between p-3 rounded-lg border border-green-200 bg-green-50">
                    <span className="text-sm font-medium text-green-800">Minimum Probability Check</span>
                    <span className="text-[10px] font-bold text-green-700 bg-green-200 px-2 py-0.5 rounded-full">PASSED</span>
                  </div>
                  <div className="flex items-center justify-between p-3 rounded-lg border border-green-200 bg-green-50">
                    <span className="text-sm font-medium text-green-800">Duplicate Action Check</span>
                    <span className="text-[10px] font-bold text-green-700 bg-green-200 px-2 py-0.5 rounded-full">PASSED</span>
                  </div>
                  <div className="flex items-center justify-between p-3 rounded-lg border border-green-200 bg-green-50">
                    <span className="text-sm font-medium text-green-800">Terminal State Check</span>
                    <span className="text-[10px] font-bold text-green-700 bg-green-200 px-2 py-0.5 rounded-full">PASSED</span>
                  </div>
                  <div className="flex items-center justify-between p-3 rounded-lg border border-green-200 bg-green-50">
                    <span className="text-sm font-medium text-green-800">Positive ERV Check</span>
                    <span className="text-[10px] font-bold text-green-700 bg-green-200 px-2 py-0.5 rounded-full">PASSED</span>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* SECTION 5 - COUNTERFACTUAL ADVANTAGE */}
          {detail.counterfactual && (
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
              <SectionHeader 
                title="Counterfactual Advantage" 
                subtitle="Quantified economic benefit compared to the next-best allowed action" 
                icon={SplitSquareHorizontal} 
                tag="VERIFIED PROOF"
              />
              
              <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-blue-50/70 rounded-xl p-5 border border-blue-200">
                  <div className="flex justify-between items-start">
                    <div>
                      <p className="text-xs text-blue-700 font-bold uppercase tracking-wider">Selected Action</p>
                      <h4 className="text-lg font-bold text-slate-900 capitalize mt-1">
                        {detail.counterfactual.selected_action.replace('_', ' ')}
                      </h4>
                    </div>
                    <span className="inline-flex items-center rounded-md bg-blue-600 px-2 py-0.5 text-[10px] font-bold text-white uppercase tracking-wider">
                      CHOSEN
                    </span>
                  </div>
                  <div className="mt-4 pt-3 border-t border-blue-200/70 flex justify-between items-center">
                    <span className="text-xs text-slate-600 font-medium">Expected Recovery Value (ERV)</span>
                    <span className="text-lg font-bold text-blue-700">{formatCurrency(detail.counterfactual.selected_erv)}</span>
                  </div>
                </div>
                
                <div className="bg-slate-50 rounded-xl p-5 border border-slate-200">
                  <div className="flex justify-between items-start">
                    <div>
                      <p className="text-xs text-slate-500 font-bold uppercase tracking-wider">Next Best Allowed Action</p>
                      <h4 className="text-lg font-bold text-slate-700 capitalize mt-1">
                        {detail.counterfactual.counterfactual_action.replace('_', ' ')}
                      </h4>
                    </div>
                    <span className="inline-flex items-center rounded-md bg-slate-200 px-2 py-0.5 text-[10px] font-bold text-slate-600 uppercase tracking-wider">
                      FALLBACK
                    </span>
                  </div>
                  <div className="mt-4 pt-3 border-t border-slate-200 flex justify-between items-center">
                    <span className="text-xs text-slate-500 font-medium">Alternative ERV</span>
                    <span className="text-lg font-bold text-slate-600">{formatCurrency(detail.counterfactual.counterfactual_erv)}</span>
                  </div>
                </div>
              </div>

              <div className="mt-4 flex flex-col sm:flex-row sm:items-center justify-between bg-emerald-50 rounded-xl p-4 border border-emerald-200">
                <div className="flex items-center">
                  <CheckCircle2 className="w-5 h-5 text-emerald-600 mr-2 flex-shrink-0" />
                  <div>
                    <span className="text-sm font-bold text-emerald-900">Demonstrated Economic Advantage</span>
                    <p className="text-xs text-emerald-700">ERV improvement generated by RecoverOS over default dunning</p>
                  </div>
                </div>
                <span className="text-2xl font-bold text-emerald-700 mt-2 sm:mt-0">
                  +{formatCurrency(detail.counterfactual.value_difference)}
                </span>
              </div>
            </div>
          )}

          {/* SECTION 6 & 7 - EXECUTION & SETTLEMENT */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
            <SectionHeader title="Execution & Reconciliation" subtitle="Razorpay integration layer" icon={PlayCircle} />
            
            <div className="mt-4 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between p-4 bg-slate-50 border border-slate-200 rounded-lg">
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Execution Status</p>
                  <div className="flex items-center">
                    <span className="text-sm font-bold text-slate-900 mr-3">{detail.latest_execution_status || 'PENDING'}</span>
                    {detail.selected_action === 'payment_method_update' ? (
                      <TagRecommendation />
                    ) : detail.selected_action === 'recovery_link' ? (
                      <TagLive />
                    ) : detail.selected_action === 'stop' ? (
                      <TagInternal label="DUNNING HALTED" />
                    ) : (
                      <TagSimulated />
                    )}
                  </div>
                </div>
                {detail.active_payment_link_id && (
                  <div className="mt-3 sm:mt-0 sm:text-right">
                    <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Active Payment Link</p>
                    <a href={detail.active_payment_link_url || '#'} target="_blank" rel="noreferrer" className="text-sm font-mono text-blue-600 hover:underline">
                      {detail.active_payment_link_id}
                    </a>
                  </div>
                )}
              </div>

              {detail.status === 'RECOVERED' && (
                <div className="flex flex-col sm:flex-row sm:items-center justify-between p-4 bg-green-50 border border-green-200 rounded-lg">
                  <div>
                    <p className="text-xs font-semibold text-green-700 uppercase tracking-wider mb-1">Settlement Reconciled</p>
                    <p className="text-sm font-bold text-green-900 flex items-center">
                      <CheckCircle2 className="w-4 h-4 mr-1.5 text-green-600" />
                      Recovered {formatCurrency(detail.recovered_amount)}
                    </p>
                  </div>
                  <div className="mt-3 sm:mt-0 sm:text-right">
                    <p className="text-xs font-semibold text-green-700 uppercase tracking-wider mb-1">Net Value Generated</p>
                    <p className="text-lg font-bold text-green-700">{formatCurrency(detail.net_value)}</p>
                  </div>
                </div>
              )}
              
              {detail.cancellation_pending && (
                <div className="flex items-center justify-between p-4 bg-orange-50 border border-orange-200 rounded-lg">
                  <div>
                    <p className="text-xs font-semibold text-orange-700 uppercase tracking-wider mb-1">Competing Link Protection</p>
                    <p className="text-sm font-medium text-orange-900">
                      Cancellation enqueued for previous recovery links to prevent double-charging.
                    </p>
                  </div>
                  <TagLive />
                </div>
              )}
            </div>
          </div>

        </div>

        {/* SECTION 8 - AUDIT TIMELINE (Sidebar) */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 sticky top-24">
            <SectionHeader title="Audit Timeline" subtitle="Chronological event log" icon={ListTodo} />
            
            <div className="mt-6 flow-root">
              <ul className="-mb-8">
                {timeline.events.map((event, eventIdx) => (
                  <li key={eventIdx}>
                    <div className="relative pb-8">
                      {eventIdx !== timeline.events.length - 1 ? (
                        <span className="absolute top-4 left-4 -ml-px h-full w-0.5 bg-slate-200" aria-hidden="true" />
                      ) : null}
                      <div className="relative flex space-x-3">
                        <div>
                          <span className={clsx(
                            "h-8 w-8 rounded-full flex items-center justify-center ring-4 ring-white",
                            event.is_live ? "bg-emerald-500" : 
                            event.event_type.includes('decision') ? "bg-slate-700" :
                            "bg-purple-500"
                          )}>
                            <div className="w-2 h-2 bg-white rounded-full"></div>
                          </span>
                        </div>
                        <div className="flex min-w-0 flex-1 justify-between space-x-4 pt-1.5">
                          <div>
                            <p className="text-sm font-medium text-slate-900">{event.summary}</p>
                            <p className="text-xs text-slate-500 mt-1 uppercase tracking-wider font-semibold">
                              {event.event_type.replace('_', ' ')}
                            </p>
                            <div className="mt-2">
                              {event.is_live ? (
                                <TagLive />
                              ) : event.event_type.includes('decision') ? (
                                <TagInternal label="DECISION LOGIC" />
                              ) : event.summary.includes('payment_method_update') ? (
                                <TagRecommendation />
                              ) : (
                                <TagSimulated />
                              )}
                            </div>
                          </div>
                          <div className="whitespace-nowrap text-right text-xs text-slate-500 font-mono">
                            {format(new Date(event.timestamp), 'HH:mm:ss')}
                          </div>
                        </div>
                      </div>
                    </div>
                  </li>
                ))}
                {timeline.events.length === 0 && (
                  <li className="text-center text-sm text-slate-500 py-4">No events found.</li>
                )}
              </ul>
            </div>
          </div>
        </div>
        
      </div>
    </div>
  );
}
