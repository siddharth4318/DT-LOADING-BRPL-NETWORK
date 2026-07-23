import sys
sys.path.insert(0, "dashboard")
from fl_data_helpers import raw_data_glob, _files_for_month

raw_files = raw_data_glob()
print(f"Total raw files found: {len(raw_files)}")
if raw_files:
    print(f"Sample raw files: {raw_files[:3]}")
    
    # Check if _files_for_month works for raw data
    month_202106 = _files_for_month(raw_files, "202106")
    print(f"\nFiles for month 202106: {len(month_202106)}")
    if month_202106:
        print(f"Sample: {month_202106[:3]}")
    else:
        print("No files found for month 202106")
else:
    print("No raw files found")
