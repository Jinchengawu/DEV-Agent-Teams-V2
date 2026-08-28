import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Button } from "antd";
import { X } from "lucide-react";

export type InspectorTab = {
  id: string;
  label: string;
  content: ReactNode;
};

type InspectorProps = {
  open: boolean;
  kicker: string;
  title: string;
  tabs: InspectorTab[];
  footer?: ReactNode;
  onClose: () => void;
};

export function Inspector({ open, kicker, title, tabs, footer, onClose }: InspectorProps) {
  const [activeTab, setActiveTab] = useState(tabs[0]?.id ?? "");
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.documentElement.classList.add("inspector-open");
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.documentElement.classList.remove("inspector-open");
      document.removeEventListener("keydown", handleKey);
      previousFocus.current?.focus();
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!tabs.some((tab) => tab.id === activeTab)) setActiveTab(tabs[0]?.id ?? "");
  }, [activeTab, tabs]);

  if (!open) return null;
  const active = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];

  return <aside className="detail-inspector" role="dialog" aria-labelledby={titleId}>
    <header className="inspector-head">
      <div className="inspector-title">
        <div><p className="eyebrow">{kicker}</p><h2 id={titleId}>{title}</h2></div>
        <Button ref={closeRef} className="icon-button" type="text" aria-label="关闭检查器" onClick={onClose} icon={<X size={19}/>}/>
      </div>
      {tabs.length > 1 && <div className="inspector-tabs" role="tablist" aria-label="检查器内容">
        {tabs.map((tab) => <Button
          key={tab.id}
          type="text"
          role="tab"
          aria-selected={active?.id === tab.id}
          className={active?.id === tab.id ? "active" : ""}
          onClick={() => setActiveTab(tab.id)}
        >{tab.label}</Button>)}
      </div>}
    </header>
    <div className="inspector-body">{active?.content}</div>
    {footer && <footer className="inspector-foot">{footer}</footer>}
  </aside>;
}
