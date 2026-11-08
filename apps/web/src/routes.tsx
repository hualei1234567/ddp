import { Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "./components/AppLayout";
import Workbench from "./pages/Workbench";

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Workbench />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
