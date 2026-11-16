/** 场地页面的路径构造（只做编码，不承担权限判断）。 */

export const LOCATION_LIST_PATH = "/locations";

export function locationDetailPath(locationId: string): string {
  return `${LOCATION_LIST_PATH}/${encodeURIComponent(locationId)}`;
}
