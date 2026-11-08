/**
 * 设备页面主状态。实现已抽取到 `pagePhase`，这里保留设备域命名，
 * 避免已有页面与测试大范围改动。
 */
export {
  resolvePagePhase as resolveDevicePagePhase,
  type PagePhase as DevicePagePhase,
  type PagePhaseInput as DevicePagePhaseInput,
} from "./pagePhase";
