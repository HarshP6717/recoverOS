"""Pre-heuristic design calculation — run once to verify rule basis."""
BASE = {
    'insufficient_funds':    {'retry_now':0.08,'retry_later':0.68,'send_reminder':0.42,'payment_method_update':0.38,'recovery_link':0.45,'escalate_human':0.55},
    'bank_timeout':          {'retry_now':0.74,'retry_later':0.79,'send_reminder':0.18,'payment_method_update':0.12,'recovery_link':0.22,'escalate_human':0.50},
    'soft_decline':          {'retry_now':0.38,'retry_later':0.64,'send_reminder':0.36,'payment_method_update':0.46,'recovery_link':0.42,'escalate_human':0.58},
    'expired_card':          {'retry_now':0.01,'retry_later':0.02,'send_reminder':0.32,'payment_method_update':0.82,'recovery_link':0.74,'escalate_human':0.72},
    'hard_decline':          {'retry_now':0.005,'retry_later':0.01,'send_reminder':0.12,'payment_method_update':0.70,'recovery_link':0.58,'escalate_human':0.66},
    'invalid_payment_method':{'retry_now':0.01,'retry_later':0.015,'send_reminder':0.28,'payment_method_update':0.78,'recovery_link':0.72,'escalate_human':0.68},
    'customer_abandoned':    {'retry_now':0.05,'retry_later':0.12,'send_reminder':0.52,'payment_method_update':0.36,'recovery_link':0.68,'escalate_human':0.70},
    'repeated_failure':      {'retry_now':0.04,'retry_later':0.16,'send_reminder':0.22,'payment_method_update':0.62,'recovery_link':0.54,'escalate_human':0.76},
    'unknown':               {'retry_now':0.25,'retry_later':0.40,'send_reminder':0.28,'payment_method_update':0.32,'recovery_link':0.35,'escalate_human':0.52},
}
COSTS = {'retry_now':1,'retry_later':1,'send_reminder':0.5,'payment_method_update':2,'recovery_link':1.5,'escalate_human':30}
AMOUNT = 543.0

print("Base ERV by failure_type (amount=543, base probs only, no context modifiers):")
print(f"{'failure_type':<26} {'best_action':<25} {'best_erv':>10}   {'2nd_action':<25} {'2nd_erv':>10}")
print('-'*105)
for ft, probs in BASE.items():
    ranked = sorted([(act, probs[act]*AMOUNT - COSTS[act]) for act in probs], key=lambda x: -x[1])
    best = ranked[0]
    second = ranked[1]
    print(f"{ft:<26} {best[0]:<25} {best[1]:>10.2f}   {second[0]:<25} {second[1]:>10.2f}")

print()
print("Hard failures (base retry_now / retry_later probs):")
for ft in ['expired_card','hard_decline','invalid_payment_method']:
    p = BASE[ft]
    print(f"  {ft}: retry_now={p['retry_now']}, retry_later={p['retry_later']}, payment_method_update={p['payment_method_update']}")

print()
print("attempt penalty: -0.32 * (attempt-1)  | at attempt=4: -0.96 logit = significant suppression")
print("contact fatigue: -0.12 * max(0,contact-2) | at contact=5: -0.36 logit")
print("escalate_human cost = 30.  Break-even prob: 30/amount")
print(f"  at amount=200: {30/200:.3f}  at amount=500: {30/500:.3f}  at amount=1000: {30/1000:.3f}")
