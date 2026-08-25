import type { ReactNode } from "react";
import { App, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import "./design-system.css";

const tokens = {
  colorPrimary: "#155EEF",
  colorInfo: "#155EEF",
  colorSuccess: "#0E9384",
  colorWarning: "#D97706",
  colorError: "#D92D20",
  colorText: "#142033",
  colorTextSecondary: "#526074",
  colorBorder: "#D6DFEA",
  colorBorderSecondary: "#E6EBF2",
  colorBgLayout: "#F4F7FB",
  colorBgContainer: "#FFFFFF",
  colorFillAlter: "#F7F9FC",
  borderRadius: 8,
  borderRadiusLG: 12,
  controlHeight: 38,
  fontSize: 14,
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
} as const;

export function DesignSystemProvider({ children }: { children: ReactNode }) {
  return <ConfigProvider
    locale={zhCN}
    button={{ autoInsertSpace: false }}
    wave={{ disabled: true }}
    theme={{
      algorithm: theme.defaultAlgorithm,
      token: tokens,
      components: {
        Button: { fontWeight: 650, primaryShadow: "none" },
        Card: { headerBg: "#FFFFFF" },
        Input: { activeShadow: "0 0 0 3px rgba(21, 94, 239, 0.14)" },
        Select: { activeOutlineColor: "rgba(21, 94, 239, 0.14)" },
        Menu: { itemBorderRadius: 7, itemHeight: 42, itemSelectedBg: "#EAF2FF", itemSelectedColor: "#0B4F9F" },
        Modal: { titleFontSize: 18 },
        Tabs: { itemActiveColor: "#155EEF", itemSelectedColor: "#155EEF", inkBarColor: "#155EEF" },
        Tag: { borderRadiusSM: 999 },
      },
    }}
  >
    <App>{children}</App>
  </ConfigProvider>;
}
