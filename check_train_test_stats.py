import pandas as pd

df = pd.read_parquet(r'c:\Users\Siddharth Jain\Documents\BRPL DTS\PALAM\features\dt_hourly_features.parquet', 
                     columns=['HOUR_TS', 'KVA_TOTAL_AVG', 'TEMP_C'])

train = df[df['HOUR_TS'] < '2026-01-01']
test = df[df['HOUR_TS'] >= '2026-01-01']

print('TRAIN DATA (2024-2025):')
print(f'  Rows: {len(train):,}')
print(f'  KVA Mean: {train["KVA_TOTAL_AVG"].mean():.2f}')
print(f'  KVA Std: {train["KVA_TOTAL_AVG"].std():.2f}')
print(f'  KVA Min: {train["KVA_TOTAL_AVG"].min():.2f}')
print(f'  KVA Max: {train["KVA_TOTAL_AVG"].max():.2f}')
print(f'  Temp Mean: {train["TEMP_C"].mean():.2f}')

print('\nTEST DATA (2026):')
print(f'  Rows: {len(test):,}')
print(f'  KVA Mean: {test["KVA_TOTAL_AVG"].mean():.2f}')
print(f'  KVA Std: {test["KVA_TOTAL_AVG"].std():.2f}')
print(f'  KVA Min: {test["KVA_TOTAL_AVG"].min():.2f}')
print(f'  KVA Max: {test["KVA_TOTAL_AVG"].max():.2f}')
print(f'  Temp Mean: {test["TEMP_C"].mean():.2f}')

print('\nDIFFERENCE (Test - Train):')
print(f'  KVA Mean Diff: {test["KVA_TOTAL_AVG"].mean() - train["KVA_TOTAL_AVG"].mean():.2f}')
print(f'  KVA Std Diff: {test["KVA_TOTAL_AVG"].std() - train["KVA_TOTAL_AVG"].std():.2f}')
print(f'  Temp Mean Diff: {test["TEMP_C"].mean() - train["TEMP_C"].mean():.2f}')
