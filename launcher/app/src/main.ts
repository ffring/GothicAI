import { isTauri } from "@tauri-apps/api/core";
import { mount } from "svelte";
import "./fonts";
import "./app.css";

if (!isTauri()) (await import("./lib/mock")).installMock(); // a plain browser: screens without the core
import App from "./App.svelte";

export default mount(App, { target: document.getElementById("app")! });
