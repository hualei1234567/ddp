import { Route, Routes } from "react-router-dom";

import { navSections } from "./app/navigation";
import AppLayout from "./components/AppLayout";
import Forbidden from "./pages/Forbidden";
import ModulePage from "./pages/ModulePage";
import NotFound from "./pages/NotFound";
import Workbench from "./pages/Workbench";

/**
 * 已开放模块在此显式挂载页面；未开放模块统一走 ModulePage 占位。
 * 新增业务模块时不要复制布局，只追加路由与页面组件。
 */
const plannedRoutes = navSections
  .flatMap((section) => section.items)
  .filter((item) => !item.available && item.path !== "/");

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Workbench />} />
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
