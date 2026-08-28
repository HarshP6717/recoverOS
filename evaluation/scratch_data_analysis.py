import pandas as pd
import numpy as np

train = pd.read_csv('data/processed/train.csv')
test  = pd.read_csv('data/processed/test.csv')
val   = pd.read_csv('data/processed/val.csv')

print('=== TRAIN shape:', train.shape)
print('=== TEST shape:', test.shape)
print('=== VAL shape:', val.shape)
print()
print('Columns:', list(test.columns))
print()

for col in ['failure_type','payment_method','attempt_number']:
    print(f'TEST {col}:')
    print(test[col].value_counts().to_string())
    print()

print('Amount quantiles (test):')
print(test['amount'].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).to_string())
print('min:', test['amount'].min(), ' max:', test['amount'].max())
print()
print('contact_count (test):')
print(test['contact_count'].value_counts().sort_index().to_string())
print()
print('days_overdue (test):')
print(test['days_overdue'].describe().to_string())
print()

overlap = set(train['transaction_id']).intersection(set(test['transaction_id']))
print('Train/test transaction_id overlap:', len(overlap))
print()

# Rare combos in test
print('Rare failure_type+payment_method combos in test (count <= 5):')
combo_test = test.groupby(['failure_type','payment_method']).size().reset_index(name='n_test')
combo_train = train.groupby(['failure_type','payment_method']).size().reset_index(name='n_train')
merged = combo_test.merge(combo_train, on=['failure_type','payment_method'], how='left').fillna(0)
rare = merged[merged['n_test'] <= 5]
print(rare.to_string(index=False))
print()

# Combos in test absent from training
absent = merged[merged['n_train'] == 0]
print('Combos present in test but ABSENT in training:')
print(absent.to_string(index=False) if len(absent) > 0 else '  None')
