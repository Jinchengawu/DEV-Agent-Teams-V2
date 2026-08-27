import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const themeStorageKey = "agent-team-os-theme";
type Theme = "light" | "dark";

function initialTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(themeStorageKey, theme); } catch { /* 浏览器可能禁用持久化。 */ }
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (meta) meta.content = theme === "dark" ? "#1c1c1f" : "#f5f5f7";
  }, [theme]);

  const dark = theme === "dark";
  return <button
    type="button"
    className="theme-toggle"
    aria-pressed={dark}
    aria-label={dark ? "切换到浅色模式" : "切换到深色模式"}
    onClick={() => setTheme(dark ? "light" : "dark")}
  >
    {dark ? <Sun size={16}/> : <Moon size={16}/>}
    <span>{dark ? "浅色模式" : "深色模式"}</span>
  </button>;
}
