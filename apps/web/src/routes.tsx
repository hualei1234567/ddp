import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { navSections } from "./app/navigation";
import AppLayout from "./components/AppLayout";
import CollectionTaskDetailPage from "./pages/collection/CollectionTaskDetailPage";
import CollectionTaskListPage from "./pages/collection/CollectionTaskListPage";
import WorkbenchPage from "./pages/collection/WorkbenchPage";
import DeviceDetailPage from "./pages/devices/DeviceDetailPage";
import DeviceListPage from "./pages/devices/DeviceListPage";
import EpisodeDetailPage from "./pages/episodes/EpisodeDetailPage";
import EpisodeListPage from "./pages/episodes/EpisodeListPage";
import Forbidden from "./pages/Forbidden";
import ModulePage from "./pages/ModulePage";
import NotFound from "./pages/NotFound";
import RawAssetDetailPage from "./pages/rawAssets/RawAssetDetailPage";
import Workbench from "./pages/Workbench";

/**
 * 已开放模块在此显式挂载页面；未开放模块统一走 ModulePage 占位。
 * 新增业务模块时不要复制布局，只追加路由与页面组件。
 */
const plannedRoutes = navSections
  .flatMap((section) => section.items)
  .filter((item) => !item.available && item.path !== "/");

function CollectionIndexRedirect() {
  const location = useLocation();
  return (
    <Navigate
      to={{ pathname: "/collection/tasks", search: location.search }}
      replace
    />
  );
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Workbench />} />
        <Route path="devices" element={<DeviceListPage />} />
        <Route path="devices/:deviceId" element={<DeviceDetailPage />} />
        <Route path="collection" element={<CollectionIndexRedirect />} />
        <Route path="collection/tasks" element={<CollectionTaskListPage />} />
        <Route
          path="collection/tasks/:taskId"
          element={<CollectionTaskDetailPage />}
        />
        <Route
          path="collection/workbench/:taskId"
          element={<WorkbenchPage />}
        />
        <Route path="episodes" element={<EpisodeListPage />} />
        <Route path="episodes/:episodeId" element={<EpisodeDetailPage />} />
        <Route
          path="raw-assets/:assetId"
          element={<RawAssetDetailPage />}
        />
        {plannedRoutes.map((item) => (
          <Route
            key={item.path}
            path={item.path.slice(1)}
            element={<ModulePage item={item} />}
          />
        ))}
        <Route path="forbidden" element={<Forbidden />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
