import { AppProvider, useApp } from "./state/store";
import LoginGate from "./components/LoginGate";
import Workbench from "./components/Workbench";

function Shell() {
  const { authed } = useApp();
  return authed ? <Workbench /> : <LoginGate />;
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
