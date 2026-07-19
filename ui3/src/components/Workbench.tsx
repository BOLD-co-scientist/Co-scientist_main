import SessionRail from "./SessionRail";
import TopBar from "./TopBar";
import EventStream from "./EventStream";
import RightRail from "./RightRail";
import HypothesisView from "./HypothesisView";
import EvolutionView from "./EvolutionView";
import { useApp } from "../state/store";

export default function Workbench() {
  const { mainView } = useApp();
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <TopBar />
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <SessionRail />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, background: "var(--bg0)" }}>
          {mainView === "session" && <EventStream />}
          {mainView === "hypothesis" && <HypothesisView />}
          {mainView === "evolution" && <EvolutionView />}
        </div>
        {mainView === "session" && <RightRail />}
      </div>
    </div>
  );
}
