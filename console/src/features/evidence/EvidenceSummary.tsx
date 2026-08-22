import { type EvidenceRecord } from "../../shared/api/client";

type EvidenceSummaryProps = {
  records: EvidenceRecord[];
};

function countStatuses(records: EvidenceRecord[]) {
  return records.reduce((acc, record) => {
    acc.total += 1;

    if (record.status === "verified") {
      acc.verified += 1;
    }
    if (record.status === "invalid") {
      acc.invalid += 1;
    }
    if (record.status === "unavailable") {
      acc.unavailable += 1;
    }

    return acc;
  }, { total: 0, verified: 0, invalid: 0, unavailable: 0 });
}

export function EvidenceSummary({ records }: EvidenceSummaryProps) {
  const { total, verified, invalid, unavailable } = countStatuses(records);

  return (
    <dl className="evidence-summary">
      <div className="summary-card">
        <dt>证据总数</dt>
        <dd className="summary-value summary-value--neutral" data-stat="total">{total}</dd>
      </div>
      <div className="summary-card">
        <dt>已验证</dt>
        <dd className="summary-value summary-value--verified" data-stat="verified">{verified}</dd>
      </div>
      <div className="summary-card">
        <dt>无效</dt>
        <dd className="summary-value summary-value--invalid" data-stat="invalid">{invalid}</dd>
      </div>
      <div className="summary-card">
        <dt>不可用</dt>
        <dd className="summary-value summary-value--neutral" data-stat="unavailable">{unavailable}</dd>
      </div>
    </dl>
  );
}
