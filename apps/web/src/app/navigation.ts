import {
  AppstoreOutlined,
  AuditOutlined,
  BarChartOutlined,
  CloudUploadOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  EnvironmentOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  HomeOutlined,
  PartitionOutlined,
  ReadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  TagsOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  ToolOutlined,
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

/**
 * 顶部阶段标签的**静态兜底**。
 *
 * 真实阶段以服务端 `/platform/meta` 的 `phase` 为准（工作台展示的是它）。
 * 这里只在下拉菜单这类拿不到异步数据的静态位置使用，因此必须与当前
 * 服务端默认值保持一致；服务端改阶段时这里要同步。
 */
export const PLATFORM_STAGE_LABEL = "阶段 8 · 规模化运营";

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
        available: true,
      },
      {
        // 真实页面挂在 /data-requirements（routes.tsx）；此前导航写成
        // /requirements 并置 available=false，导致完整实现的页面在菜单里
        // 是个永远点不动的禁用项，用户无法从导航到达。
        path: "/data-requirements",
        label: "数据需求",
        description: "登记数据需求、场景规划与采集目标。",
        icon: createElement(FileTextOutlined),
        available: true,
      },
      {
        path: "/collection",
        label: "数据采集",
        description: "管理采集任务、现场作业、SOP 与人员设备排期。",
        icon: createElement(ExperimentOutlined),
        available: true,
      },
      {
        // 阶段 8 的四类素材（方案 / SOP / 场景 / 动作库）此前后端已就绪、
        // 前端零页面。入口独立于「数据采集」任务，避免把素材管理与现场作业
        // 混在同一个列表里。
        path: "/collection-assets",
        label: "采集素材",
        description: "管理采集方案、SOP、场景与动作库；状态动作以服务端返回为准。",
        icon: createElement(ReadOutlined),
        available: true,
      },
      {
        path: "/ingestion",
        label: "数据接入",
        description: "登记外部数据源（Hugging Face）并发起小样本导入。",
        icon: createElement(CloudUploadOutlined),
        available: true,
        aliases: ["/data-source-imports"],
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
        // 后端 N4 质量能力（黄金集 / 抽检 / IAA / 提交质量检查）早已就绪，
        // 此前导航写成 available:false，用户永远进不到这些页面。
        path: "/quality",
        label: "质量管理",
        description:
          "金标准集（黄金集）、质检抽检、IAA 报告与提交质量检查；无结论不等于通过。",
        icon: createElement(SafetyCertificateOutlined),
        available: true,
      },
      {
        // 后端 `GET /raw-assets` 早已就绪，但此前菜单是不可进入的占位页：
        // 数据在库里，用户没有入口。现在挂真实列表页（检索 + 跳转详情）。
        path: "/assets",
        label: "数据资产",
        description: "检索项目内经校验的原始资产（上传 / 设备采集 / 数据源导入）。",
        icon: createElement(FolderOpenOutlined),
        available: true,
      },
      {
        path: "/datasets",
        label: "数据集管理",
        description: "构建、冻结与交付训练数据集。",
        icon: createElement(DatabaseOutlined),
        available: true,
      },
      {
        path: "/exports",
        label: "导出任务",
        description: "管理异步导出、产物清单与下载。",
        icon: createElement(CloudUploadOutlined),
        available: true,
      },
      {
        path: "/training-requests",
        label: "训练支撑",
        description: "管理训练需求、交付与外部训练引用。",
        icon: createElement(ThunderboltOutlined),
        available: true,
        aliases: ["/training", "/training-deliveries"],
      },
      {
        path: "/models",
        label: "模型与推理",
        description: "管理模型版本、评测、推理服务、模型反馈与数据缺口闭环。",
        icon: createElement(RobotOutlined),
        available: true,
        aliases: [
          "/evaluations",
          "/model-feedback",
          "/data-gaps",
          "/inference-services",
          "/inference-tasks",
        ],
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
        description: "管理设备、传感器、标定信息与设备状态。",
        icon: createElement(ClusterOutlined),
        available: true,
      },
      {
        // 后端 `/robots` 的列表、创建、修改与 5 个生命周期动作早已实现，
        // 但前端此前零页面、零引用：设备与传感器都靠 robot_id 挂载，
        // 没有机器人入口就没法完成纳管链路。
        path: "/robots",
        label: "机器人",
        description:
          "登记机器人档案与生命周期状态；设备、传感器与采集任务通过它挂载。",
        icon: createElement(ToolOutlined),
        available: true,
      },
      {
        // 阶段 9 的场地实体（基地 / 房间 / 实验室）已实现，单独成为可到达
        // 的菜单项；「基地运营」里的排期与现场运营仍是规划能力，保持占位。
        path: "/locations",
        label: "场地管理",
        description: "管理基地、房间与实验室场地实体；被设备与采集任务引用。",
        icon: createElement(EnvironmentOutlined),
        available: true,
      },
      {
        path: "/people",
        label: "人员与团队",
        description: "管理采集、标注、质检等角色与团队。",
        icon: createElement(TeamOutlined),
        available: true,
      },
      {
        path: "/suppliers",
        label: "供应商与交付",
        description: "供应商档案、到货批次、片段裁决与验收核验账本。",
        icon: createElement(TruckOutlined),
        available: true,
        aliases: ["/inbound-batches", "/acceptance-ledger"],
      },
    ],
  },
  {
    key: "governance",
    label: "平台治理",
    items: [
      {
        // N9 配额与用量报表：后端 `GET /usage-report`、`GET /quotas`、
        // `PUT /quotas/{kind}` 已就绪且契约已收录；此前菜单是禁用项，
        // 用户永远到不了「用多少、剩多少、超没超」。
        path: "/analytics",
        label: "经营分析",
        description:
          "项目级资源用量与配额对照：已用 / 上限 / 剩余 / 状态 / 统计截止时间；未测量与 0 严格区分。",
        icon: createElement(BarChartOutlined),
        available: true,
      },
      {
        // 平台一直在写 audit_logs，这里补上唯一的读入口：谁在什么时候
        // 对什么资源做了什么。只读且仅项目负责人/平台管理员可看。
        path: "/audit-logs",
        label: "审计日志",
        description: "按资源、动作、时间与结果追溯全项目的操作留痕。",
        icon: createElement(AuditOutlined),
        available: true,
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
