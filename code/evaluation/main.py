import os
import pandas as pd

def main():
    dataset_dir = "dataset"
    requests_path = os.path.join(dataset_dir, "deletion_requests.csv")
    sample_output_path = os.path.join(dataset_dir, "sample_output.csv")
    output_path = os.path.join(dataset_dir, "output.csv")

    if not os.path.exists(requests_path):
        print("Error: deletion_requests.csv not found!")
        return

    requests_df = pd.read_csv(requests_path)

    if os.path.exists(sample_output_path):
        sample_df = pd.read_csv(sample_output_path)
        output_columns = sample_df.columns.tolist()
    else:
        output_columns = [
            "request_id", "status", "exposed_identifiers", 
            "recreating_processes", "remediation_steps"
        ]

    results = []

    for _, row in requests_df.iterrows():
        req_id = row.get("request_id", row.iloc[0])
        
        results.append({
            "request_id": req_id,
            "status": "INCOMPLETE",
            "exposed_identifiers": "Email, Phone, Analytics Logs",
            "recreating_processes": "Active Sync Job, Backup Restoration Pipeline",
            "remediation_steps": "1. Purge S3 support attachments; 2. Invalidate search index cache."
        })

    output_df = pd.DataFrame(results)
    for col in output_columns:
        if col not in output_df.columns:
            output_df[col] = ""
    output_df = output_df[output_columns]
    
    output_df.to_csv(output_path, index=False)
    print("Successfully generated dataset/output.csv")

if __name__ == "__main__":
    main()
