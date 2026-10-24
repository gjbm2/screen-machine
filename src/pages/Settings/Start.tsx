// /start: where the app opens (ia 24 Oct, §3): this device's choice in Settings › This device ("Open the app on":
// Room or Music, sm_home), else the Room page. A redirect only (replace, so Back doesn't land here again); the rest of
// the address (?room=, a #fold) goes along.

import { Navigate, useLocation } from "react-router-dom";
import { readHome } from "./deviceStore";

export default function StartPage() {
  const { search, hash } = useLocation();
  return <Navigate to={`${readHome() ?? "/room"}${search}${hash}`} replace />;
}
