import {
  AppstoreOutlined,
  BarChartOutlined,
  CloudUploadOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  EnvironmentOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  HomeOutlined,
  PartitionOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TagsOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  TruckOutlined,
} from "@ant-design/icons";
import { createElement, type ReactNode } from "react";

export interface NavItem {
  /** 路由路径，同时作为菜单 key。 */
  path: string;
  label: string;
  /** 占位页展示的模块职责说明，不承载业务规则。 */
  description: string;
  icon: ReactNode;
  /** 阶段 0 仅工作台开放；其余模块先完成路由与布局骨架。 */
  available: boolean;
  /**
   * 同一模块的附加路径前缀。例如标注中心主入口为 /annotation-tasks，
   * 工作台位于 /annotation/workbench/*，两者应高亮同一菜单项。
   */
  aliases?: string[];
}

export interface NavSection {
  key: string;
  label: string;
  items: NavItem[];
}

export const PLATFORM_STAGE_LABEL = "阶段 0 · 基建";

/**
 * 一级导航分区，依据规范 §18.2。
 * 新增模块时只改这里与 `routes.tsx` 的显式页面映射，不在布局组件里分散判断。
 */
export const navSections: NavSection[] = [
  {
    key: "overview",
    label: "总览",
    items: [
      {
        path: "/",
        label: "工作台",
        description: "查看平台服务状态、元信息与阶段基建进度。",
        icon: createElement(HomeOutlined),
        available: true,
      },
    ],
  },
  {
    key: "pipeline",
    label: "数据链路",
    items: [
      {
        path: "/projects",
        label: "项目管理",
        description: "管理项目、采集方案与项目范围内的数据资产。",
        icon: createElement(AppstoreOutlined),
        available: false,
      },
      {
        path: "/requirements",
        label: "数据需求",
        description: "登记数据需求、场景规划与采集目标。",
        icon: createElement(FileTextOutlined),
        available: false,
      },
      {
        path: "/collection",
        label: "数据采集",
        description: "管理采集任务、现场作业、SOP 与人员设备排期。",
        icon: createElement(ExperimentOutlined),
        available: true,
      },
      {
        path: "/ingestion",
        label: "数据接入",
        description: "接收原始数据，执行完整性校验与格式解析。",
        icon: createElement(CloudUploadOutlined),
        available: false,
      },
      {
        path: "/processing",
        label: "数据处理",
        description: "执行清洗、预处理、时间同步与 Episode 切片。",
        icon: createElement(DeploymentUnitOutlined),
        available: false,
      },
      {
        path: "/episodes",
        label: "Episode 管理",
        description: "查看切片结果、元数据与数据血缘。",
        icon: createElement(PartitionOutlined),
        available: true,
      },
      {
        path: "/annotation-tasks",
        label: "标注中心",
        description: "管理标注 Schema、样本集、任务、工作台、复核与仲裁。",
        icon: createElement(TagsOutlined),
        available: true,
        aliases: ["/annotation"],
      },
      {
        path: "/quality",
        label: "质量管理",
        description: "执行质检规则、抽检与质量门禁。",
        icon: createElement(SafetyCertificateOutlined),
        available: false,
      },
      {
        path: "/assets",
        label: "数据资产",
        description: "管理数据资产的检索、引用与版本。",
        icon: createElement(FolderOpenOutlined),
        available: false,
      },
      {
        path: "/datasets",
        label: "数据集管理",
        description: "构建、冻结与交付训练数据集。",
        icon: createElement(DatabaseOutlined),
        available: false,
      },
      {
        path: "/training",
        label: "训练支撑",
        description: "管理训练任务、训练数据与产物登记。",
        icon: createElement(ThunderboltOutlined),
        available: false,
      },
      {
        path: "/models",
        label: "模型与推理",
        description: "管理模型版本、推理服务与评测结果。",
        icon: createElement(RobotOutlined),
        available: false,
      },
    ],
  },
  {
    key: "resources",
    label: "资源与运营",
    items: [
      {
        path: "/devices",
        label: "设备纳管",
        description: "管理机器人、传感器、标定信息与设备状态。",
        icon: createElement(ClusterOutlined),
        available: true,
      },
      {
        path: "/operations",
        label: "基地运营",
        description: "管理场地、排期、现场运营与任务执行情况。",
        icon: createElement(EnvironmentOutlined),
        available: false,
      },
      {
        path: "/people",
        label: "人员与团队",
        description: "管理采集、标注、质检等角色与团队。",
        icon: createElement(TeamOutlined),
        available: false,
      },
      {
        path: "/suppliers",
        label: "供应商与交付",
        description: "管理供应商、交付验收与结算依据。",
        icon: createElement(TruckOutlined),
        available: false,
      },
    ],
  },
  {
    key: "governance",
    label: "平台治理",
    items: [
      {
        path: "/analytics",
        label: "经营分析",
        description: "查看运营指标、产能与成本分析。",
        icon: createElement(BarChartOutlined),
        available: false,
      },
      {
        path: "/settings",
        label: "系统管理",
        description: "管理账号、权限、字典与平台配置。",
        icon: createElement(SettingOutlined),
        available: false,
      },
    ],
  },
];

export interface NavMatch {
  section: NavSection;
  item: NavItem;
}

/** 按最长前缀匹配当前路由，保证详情页仍高亮所属模块。 */
export function findNavItem(pathname: string): NavMatch | null {
  let match: NavMatch | null = null;

  for (const section of navSections) {
    for (const item of section.items) {
      for (const candidate of [item.path, ...(item.aliases ?? [])]) {
        const isExact = candidate === pathname;
        const isNested =
          candidate !== "/" && pathname.startsWith(`${candidate}/`);
        if (!isExact && !isNested) {
          continue;
        }
        if (match === null || candidate.length > match.item.path.length) {
          match = { section, item };
        }
      }
    }
  }

  return match;
}
