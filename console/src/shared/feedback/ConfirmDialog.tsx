import { useEffect, useRef } from "react";
import { Button } from "antd";
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
  const dialogRef = useRef<HTMLElement>(null);
  const cancelHandlerRef = useRef(onCancel);
  cancelHandlerRef.current = onCancel;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    cancelRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) {
        event.preventDefault();
        cancelHandlerRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])") ?? []);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => { document.removeEventListener("keydown", handleKeyDown); document.body.style.overflow = previousOverflow; previous?.focus(); };
  }, [open, pending]);
  if (!open) return null;
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target && !pending) onCancel(); }}>
    <section ref={dialogRef} className={`confirm-dialog confirm-dialog--${tone}`} role="alertdialog" aria-modal="true" aria-labelledby="confirm-dialog-title" aria-describedby="confirm-dialog-detail">
      <AlertTriangle size={22}/><div><span className="eyebrow">高风险操作</span><h2 id="confirm-dialog-title">{title}</h2><p id="confirm-dialog-detail">{detail}</p></div>
      <div className="confirm-dialog__actions"><Button aria-label={cancelLabel} ref={cancelRef} disabled={pending} onClick={onCancel}>{cancelLabel}</Button><Button aria-label={pending ? "正在执行" : confirmLabel} type="primary" danger={tone === "danger"} loading={pending} disabled={pending} onClick={onConfirm}>{pending ? "正在执行…" : confirmLabel}</Button></div>
    </section>
  </div>;
}
