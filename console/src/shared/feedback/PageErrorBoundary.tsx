import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

type State = { error?: Error };

export class PageErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = {};
  static getDerivedStateFromError(error: Error): State { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("控制台模块渲染失败", error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return <section className="page-crash panel" role="alert"><AlertTriangle size={24}/><div><span className="eyebrow">模块运行异常</span><h2>当前页面没有完成渲染</h2><p>已阻止异常扩散到整个控制台。刷新当前模块后重试；如果问题持续存在，请保留当前地址并查看服务日志。</p></div><button className="primary" onClick={() => window.location.reload()}><RotateCcw size={16}/>刷新当前模块</button></section>;
  }
}
