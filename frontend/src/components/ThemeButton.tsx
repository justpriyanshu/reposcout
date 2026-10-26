import { useTheme } from "@/hooks/useTheme";
import { Button } from "./ui/button";
import { Icon } from "./Icon";

export function ThemeButton() {
  const { dark, toggle } = useTheme();
  return (
    <Button variant="quiet" size="icon" id="themeBtn" aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>
      <Icon name={dark ? "sun" : "moon"} size={18} />
    </Button>
  );
}
