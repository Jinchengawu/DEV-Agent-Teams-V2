import { type ReactNode, useEffect, useState } from "react";
import { App, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { themeChangeEvent } from "../../shared/ui/ThemeToggle";
import "./design-system.css";

type ThemeMode = "light" | "dark";

const lightTokens = {
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

const darkTokens = {
  ...lightTokens,
  colorPrimary: "#70A7FF",
  colorInfo: "#70A7FF",
  colorSuccess: "#63D6A0",
  colorWarning: "#F5C451",
  colorError: "#FF8B82",
  colorText: "#F0F0F2",
  colorTextSecondary: "#C6C6CC",
  colorBorder: "#505057",
  colorBorderSecondary: "#3A3A40",
  colorBgLayout: "#17171A",
  colorBgContainer: "#232327",
  colorFillAlter: "#2B2B30",
} as const;

function currentTheme(): ThemeMode {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function DesignSystemProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(currentTheme);
  useEffect(() => {
    const onThemeChange = (event: Event) => setMode((event as CustomEvent<ThemeMode>).detail);
    window.addEventListener(themeChangeEvent, onThemeChange);
    return () => window.removeEventListener(themeChangeEvent, onThemeChange);
  }, []);
  const dark = mode === "dark";
  const tokens = dark ? darkTokens : lightTokens;

  return <ConfigProvider
    locale={zhCN}
    button={{ autoInsertSpace: false }}
    wave={{ disabled: true }}
    theme={{
      algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
      token: tokens,
      components: {
        Button: { fontWeight: 650, primaryShadow: "none" },
        Card: { headerBg: dark ? "#232327" : "#FFFFFF" },
        Input: { activeShadow: "0 0 0 3px rgba(21, 94, 239, 0.14)" },
        Select: { activeOutlineColor: "rgba(21, 94, 239, 0.14)" },
        Menu: { itemBorderRadius: 7, itemHeight: 42, itemSelectedBg: dark ? "#29364B" : "#EAF2FF", itemSelectedColor: dark ? "#CFE0FF" : "#0B4F9F" },
        Modal: { titleFontSize: 18 },
        Tabs: { itemActiveColor: "#155EEF", itemSelectedColor: "#155EEF", inkBarColor: "#155EEF" },
        Tag: { borderRadiusSM: 999 },
      },
    }}
  >
    <App>{children}</App>
  </ConfigProvider>;
}
