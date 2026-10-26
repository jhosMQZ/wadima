import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Spinner, Toasts } from "./components/ui";
import { AreaDetailPage, AreasPage } from "./pages/Areas";
import { AreaSelect } from "./pages/AreaSelect";
import { ChangePassword, Login } from "./pages/Login";
import { useBrand } from "./store/brand";
import { useSession } from "./store/session";

const OfficePage = lazy(() => import("./pages/Office"));
const OpsPage = lazy(() => import("./pages/Ops"));
const ProjectsPage = lazy(() => import("./pages/Projects"));
const ProjectDetailPage = lazy(() => import("./pages/ProjectDetail"));
const NoteEditorPage = lazy(() => import("./pages/NoteEditor"));
const LibraryPage = lazy(() => import("./pages/Library"));
const DocumentPage = lazy(() => import("./pages/DocumentDetail"));
const MemoryPage = lazy(() => import("./pages/Memory"));
const AgentsPage = lazy(() => import("./pages/Agents"));
const MeetingsPage = lazy(() => import("./pages/Meetings"));
const StudioPage = lazy(() => import("./pages/Studio"));
const ConnectionsPage = lazy(() => import("./pages/Connections"));
const SecurityPage = lazy(() => import("./pages/Security"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const AdminPage = lazy(() => import("./pages/Admin"));
const TimePage = lazy(() => import("./pages/Time"));
const AISettingsPage = lazy(() => import("./pages/AISettings"));
const SetupPage = lazy(() => import("./pages/Setup"));

function Workspace() {
  return (
    <Layout>
      <Suspense fallback={<Spinner />}>
        <Routes>
          <Route path="/" element={<OfficePage />} />
          <Route path="/ops" element={<OpsPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/notes/:id" element={<NoteEditorPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/library/:id" element={<DocumentPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/ai" element={<AISettingsPage />} />
          <Route path="/meetings" element={<MeetingsPage />} />
          <Route path="/studio" element={<StudioPage />} />
          <Route path="/studio/:id" element={<StudioPage />} />
          <Route path="/connections" element={<ConnectionsPage />} />
          <Route path="/time" element={<TimePage />} />
          <Route path="/areas" element={<AreasPage />} />
          <Route path="/areas/:id" element={<AreaDetailPage />} />
          <Route path="/join" element={<AreaSelect embedded />} />
          <Route path="/security" element={<SecurityPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}

export default function App() {
  const { me, loading, init } = useSession();
  const platform = useBrand((s) => s.org.platform_name);
  useEffect(() => {
    void useBrand.getState().load();
    init();
  }, [init]);
  useEffect(() => {
    document.title = platform;
  }, [platform]);

  let body;
  if (loading) {
    body = (
      <div className="login-wrap">
        <Spinner label={`Iniciando ${platform}`} />
      </div>
    );
  } else if (!me) {
    body = <Login />;
  } else if (me.user.must_change_password) {
    body = <ChangePassword />;
  } else if (me.setup_required) {
    body = (
      <Suspense fallback={<Spinner />}>
        <SetupPage firstRun />
      </Suspense>
    );
  } else {
    body = <Workspace />;
  }
  return (
    <BrowserRouter>
      {body}
      <Toasts />
    </BrowserRouter>
  );
}
