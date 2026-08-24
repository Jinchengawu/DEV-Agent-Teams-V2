import { useEffect, useRef } from "react";
import { AlertTriangle } from "lucide-react";

export function ConfirmDialog({ open, title, detail, confirmLabel, cancelLabel = "取消", tone = "warning", pending = false, onConfirm, onCancel }: {
  open: boolean;
  title: string;
  detail: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "warning" | "danger";
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    cancelRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape" && !pending) onCancel(); };
    document.addEventListener("keydown", handleKeyDown);
    return () => { document.removeEventListener("keydown", handleKeyDown); previous?.focus(); };
  }, [onCancel, open, pending]);
  if (!open) return null;
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target && !pending) onCancel(); }}>
    <section className={`confirm-dialog confirm-dialog--${tone}`} role="alertdialog" aria-modal="true" aria-labelledby="confirm-dialog-title" aria-describedby="confirm-dialog-detail">
      <AlertTriangle size={22}/><div><span className="eyebrow">高风险操作</span><h2 id="confirm-dialog-title">{title}</h2><p id="confirm-dialog-detail">{detail}</p></div>
      <div className="confirm-dialog__actions"><button ref={cancelRef} className="secondary" disabled={pending} onClick={onCancel}>{cancelLabel}</button><button className={tone === "danger" ? "danger danger--filled" : "primary"} disabled={pending} onClick={onConfirm}>{pending ? "正在执行…" : confirmLabel}</button></div>
    </section>
  </div>;
}
