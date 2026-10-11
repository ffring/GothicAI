import { isTauri } from "@tauri-apps/api/core";
import { mount } from "svelte";
import "./fonts";
import "./app.css";

if (!isTauri()) (await import("./lib/mock")).installMock(); // a plain browser: screens without the core
import Tray from "./Tray.svelte";

export default mount(Tray, { target: document.getElementById("app")! });
