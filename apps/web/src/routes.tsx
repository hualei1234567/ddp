import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";

import LegacyRedirect from "./app/LegacyRedirect";
import { navSections } from "./app/navigation";
import AppLayout from "./components/AppLayout";
import AnnotationArbitrationQueuePage from "./pages/annotation/AnnotationArbitrationQueuePage";
import AnnotationGoldSetDetailPage from "./pages/annotation/AnnotationGoldSetDetailPage";
import AnnotationGoldSetListPage from "./pages/annotation/AnnotationGoldSetListPage";
import AnnotationIAADetailPage from "./pages/annotation/AnnotationIAADetailPage";
import AnnotationIAAListPage from "./pages/annotation/AnnotationIAAListPage";
import AnnotationQualityDetailPage from "./pages/annotation/AnnotationQualityDetailPage";
import AnnotationQualityListPage from "./pages/annotation/AnnotationQualityListPage";
import AnnotationReviewQueuePage from "./pages/annotation/AnnotationReviewQueuePage";
import AnnotationSampleSetDetailPage from "./pages/annotation/AnnotationSampleSetDetailPage";
import AnnotationSampleSetListPage from "./pages/annotation/AnnotationSampleSetListPage";
import AnnotationSchemaDetailPage from "./pages/annotation/AnnotationSchemaDetailPage";
import AnnotationSchemaListPage from "./pages/annotation/AnnotationSchemaListPage";
import AnnotationSubmissionPage from "./pages/annotation/AnnotationSubmissionPage";
import AnnotationTaskCreatePage from "./pages/annotation/AnnotationTaskCreatePage";
import AnnotationTaskDetailPage from "./pages/annotation/AnnotationTaskDetailPage";
import AnnotationTaskListPage from "./pages/annotation/AnnotationTaskListPage";
import AnnotationWorkbenchPage from "./pages/annotation/AnnotationWorkbenchPage";
import AuditLogListPage from "./pages/audit/AuditLogListPage";
import CollectionTaskDetailPage from "./pages/collection/CollectionTaskDetailPage";
import CollectionAssetDetailPage from "./pages/collectionAssets/AssetDetailPage";
import CollectionAssetListPage from "./pages/collectionAssets/AssetListPage";
import AssetReviewPage from "./pages/datasources/AssetReviewPage";
import DataSourceListPage from "./pages/datasources/DataSourceListPage";
import ImportListPage from "./pages/datasources/ImportListPage";
import ImportReviewPage from "./pages/datasources/ImportReviewPage";
import DatasetDetailPage from "./pages/datasets/DatasetDetailPage";
import DatasetListPage from "./pages/datasets/DatasetListPage";
import DatasetVersionDetailPage from "./pages/datasets/DatasetVersionDetailPage";
import ExportDetailPage from "./pages/datasets/ExportDetailPage";
import ExportListPage from "./pages/datasets/ExportListPage";
import TrainingDeliveryDetailPage from "./pages/datasets/TrainingDeliveryDetailPage";
import TrainingRequestDetailPage from "./pages/datasets/TrainingRequestDetailPage";
import TrainingRequestListPage from "./pages/datasets/TrainingRequestListPage";
import CollectionTaskListPage from "./pages/collection/CollectionTaskListPage";
import WorkbenchPage from "./pages/collection/WorkbenchPage";
import DeviceDetailPage from "./pages/devices/DeviceDetailPage";
import DeviceListPage from "./pages/devices/DeviceListPage";
import EpisodeDetailPage from "./pages/episodes/EpisodeDetailPage";
import EpisodeListPage from "./pages/episodes/EpisodeListPage";
import Forbidden from "./pages/Forbidden";
import LocationDetailPage from "./pages/locations/LocationDetailPage";
import LocationListPage from "./pages/locations/LocationListPage";
import DataGapPage from "./pages/models/DataGapPage";
import EvaluationDetailPage from "./pages/models/EvaluationDetailPage";
import EvaluationListPage from "./pages/models/EvaluationListPage";
import FeedbackDetailPage from "./pages/models/FeedbackDetailPage";
import FeedbackListPage from "./pages/models/FeedbackListPage";
import InferenceServiceListPage from "./pages/models/InferenceServiceListPage";
import InferenceTaskDetailPage from "./pages/models/InferenceTaskDetailPage";
import InferenceTaskListPage from "./pages/models/InferenceTaskListPage";
import ModelDetailPage from "./pages/models/ModelDetailPage";
import ModelListPage from "./pages/models/ModelListPage";
import RequirementDetailPage from "./pages/models/RequirementDetailPage";
import RequirementListPage from "./pages/models/RequirementListPage";
import BatchDetailPage from "./pages/suppliers/BatchDetailPage";
import BatchListPage from "./pages/suppliers/BatchListPage";
import LedgerPage from "./pages/suppliers/LedgerPage";
import SegmentReviewPage from "./pages/suppliers/SegmentReviewPage";
import SupplierDetailPage from "./pages/suppliers/SupplierDetailPage";
import SupplierListPage from "./pages/suppliers/SupplierListPage";
import ModulePage from "./pages/ModulePage";
import QualityCheckPage from "./pages/quality/QualityCheckPage";
import QualityOverviewPage from "./pages/quality/QualityOverviewPage";
import NotFound from "./pages/NotFound";
import PeoplePage from "./pages/people/PeoplePage";
import ProjectListPage from "./pages/projects/ProjectListPage";
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

function CollectionAssetIndexRedirect() {
  const location = useLocation();
  return (
    <Navigate
      to={{ pathname: "/collection-assets/plans", search: location.search }}
      replace
    />
  );
}

function TrainingIndexRedirect() {
  const location = useLocation();
  return (
    <Navigate
      to={{ pathname: "/training-requests", search: location.search }}
      replace
    />
  );
}

function AnnotationIndexRedirect() {
  const location = useLocation();
  return (
    <Navigate
      to={{ pathname: "/annotation-tasks", search: location.search }}
      replace
    />
  );
}

/** 带路径参数的旧地址重定向：保留查询串，只替换路径前缀。 */
function LegacyParamRedirect({
  prefix,
  param,
}: {
  prefix: string;
  param: string;
}) {
  const params = useParams();
  const value = params[param] ?? "";
  return <LegacyRedirect to={`${prefix}/${encodeURIComponent(value)}`} />;
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Workbench />} />
        <Route path="projects" element={<ProjectListPage />} />
        <Route path="people" element={<PeoplePage />} />
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
        <Route path="collection-assets" element={<CollectionAssetIndexRedirect />} />
        <Route
          path="collection-assets/:assetType"
          element={<CollectionAssetListPage />}
        />
        <Route
          path="collection-assets/:assetType/:assetId"
          element={<CollectionAssetDetailPage />}
        />
        <Route path="locations" element={<LocationListPage />} />
        <Route path="locations/:locationId" element={<LocationDetailPage />} />
        <Route path="episodes" element={<EpisodeListPage />} />
        <Route path="episodes/:episodeId" element={<EpisodeDetailPage />} />
        <Route path="raw-assets/:assetId" element={<RawAssetDetailPage />} />
        <Route path="annotation" element={<AnnotationIndexRedirect />} />
        <Route path="annotation-tasks" element={<AnnotationTaskListPage />} />
        <Route
          path="annotation-tasks/new"
          element={<AnnotationTaskCreatePage />}
        />
        <Route
          path="annotation-tasks/:taskId"
          element={<AnnotationTaskDetailPage />}
        />
        <Route
          path="annotation/workbench/:assignmentId"
          element={<AnnotationWorkbenchPage />}
        />
        <Route
          path="annotation/submissions/:submissionId"
          element={<AnnotationSubmissionPage />}
        />
        <Route path="annotation/reviews" element={<AnnotationReviewQueuePage />} />
        <Route
          path="annotation/arbitrations"
          element={<AnnotationArbitrationQueuePage />}
        />
        <Route path="annotation/schemas" element={<AnnotationSchemaListPage />} />
        <Route
          path="annotation/schemas/:schemaId"
          element={<AnnotationSchemaDetailPage />}
        />
        <Route
          path="annotation/sample-sets"
          element={<AnnotationSampleSetListPage />}
        />
        <Route
          path="annotation/sample-sets/:sampleSetId"
          element={<AnnotationSampleSetDetailPage />}
        />
        {/*
          质量管理（导航「质量管理」）：黄金集 / 抽检 / IAA / 提交质量检查。
          页面组件沿用标注域实现的同一批组件，不在 /quality 下重写一份。
        */}
        <Route path="quality" element={<QualityOverviewPage />} />
        <Route path="quality/gold-sets" element={<AnnotationGoldSetListPage />} />
        <Route
          path="quality/gold-sets/:goldSetId"
          element={<AnnotationGoldSetDetailPage />}
        />
        <Route path="quality/iaa-reports" element={<AnnotationIAAListPage />} />
        <Route
          path="quality/iaa-reports/:reportId"
          element={<AnnotationIAADetailPage />}
        />
        <Route
          path="quality/quality-samples"
          element={<AnnotationQualityListPage />}
        />
        <Route
          path="quality/quality-samples/:qualitySampleId"
          element={<AnnotationQualityDetailPage />}
        />
        <Route path="quality/quality-checks" element={<QualityCheckPage />} />
        {/*
          旧地址保留重定向（历史书签不 404）：质量资源此前挂在 /annotation 下。
        */}
        <Route
          path="annotation/gold-sets"
          element={<LegacyRedirect to="/quality/gold-sets" />}
        />
        <Route
          path="annotation/gold-sets/:goldSetId"
          element={<LegacyParamRedirect prefix="/quality/gold-sets" param="goldSetId" />}
        />
        <Route
          path="annotation/iaa-reports"
          element={<LegacyRedirect to="/quality/iaa-reports" />}
        />
        <Route
          path="annotation/iaa-reports/:reportId"
          element={<LegacyParamRedirect prefix="/quality/iaa-reports" param="reportId" />}
        />
        <Route
          path="annotation/quality-samples"
          element={<LegacyRedirect to="/quality/quality-samples" />}
        />
        <Route
          path="annotation/quality-samples/:qualitySampleId"
          element={
            <LegacyParamRedirect
              prefix="/quality/quality-samples"
              param="qualitySampleId"
            />
          }
        />
        <Route path="ingestion" element={<DataSourceListPage />} />
        <Route path="data-source-imports" element={<ImportListPage />} />
        <Route
          path="data-source-imports/:importId/assets"
          element={<AssetReviewPage />}
        />
        <Route
          path="data-source-imports/:importId/review"
          element={<ImportReviewPage />}
        />
        <Route path="datasets" element={<DatasetListPage />} />
        <Route path="datasets/:datasetId" element={<DatasetDetailPage />} />
        <Route
          path="datasets/:datasetId/versions/:versionId"
          element={<DatasetVersionDetailPage />}
        />
        <Route path="exports" element={<ExportListPage />} />
        <Route path="exports/:exportJobId" element={<ExportDetailPage />} />
        <Route path="training" element={<TrainingIndexRedirect />} />
        <Route path="training-requests" element={<TrainingRequestListPage />} />
        <Route
          path="training-requests/:requestId"
          element={<TrainingRequestDetailPage />}
        />
        <Route
          path="training-deliveries/:deliveryId"
          element={<TrainingDeliveryDetailPage />}
        />
        <Route path="models" element={<ModelListPage />} />
        <Route path="models/:modelId" element={<ModelDetailPage />} />
        <Route path="evaluations" element={<EvaluationListPage />} />
        <Route
          path="evaluations/:evaluationId"
          element={<EvaluationDetailPage />}
        />
        <Route path="model-feedback" element={<FeedbackListPage />} />
        <Route
          path="model-feedback/:feedbackId"
          element={<FeedbackDetailPage />}
        />
        <Route path="data-requirements" element={<RequirementListPage />} />
        <Route
          path="data-requirements/:requirementId"
          element={<RequirementDetailPage />}
        />
        <Route path="data-gaps" element={<DataGapPage />} />
        <Route
          path="inference-services"
          element={<InferenceServiceListPage />}
        />
        <Route path="inference-tasks" element={<InferenceTaskListPage />} />
        <Route
          path="inference-tasks/:taskId"
          element={<InferenceTaskDetailPage />}
        />
        <Route path="suppliers" element={<SupplierListPage />} />
        <Route path="suppliers/:supplierId" element={<SupplierDetailPage />} />
        <Route path="inbound-batches" element={<BatchListPage />} />
        <Route path="inbound-batches/:batchId" element={<BatchDetailPage />} />
        <Route
          path="inbound-batches/:batchId/segments/:segmentId"
          element={<SegmentReviewPage />}
        />
        <Route path="acceptance-ledger" element={<LedgerPage />} />
        <Route path="audit-logs" element={<AuditLogListPage />} />
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
