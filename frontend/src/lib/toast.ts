import { create } from "zustand";

export interface ToastItem {
  id: number;
  text: string;
  tone: "ok" | "err" | "info";
}

interface ToastState {
  items: ToastItem[];
  push: (text: string, tone?: ToastItem["tone"]) => void;
  remove: (id: number) => void;
}

let seq = 0;

export const useToasts = create<ToastState>((set, get) => ({
  items: [],
  push(text, tone = "info") {
    const id = ++seq;
    set({ items: [...get().items, { id, text, tone }].slice(-5) });
    window.setTimeout(() => get().remove(id), tone === "err" ? 8000 : 4000);
  },
  remove(id) {
    set({ items: get().items.filter((t) => t.id !== id) });
  },
}));

export const toast = (text: string, tone: ToastItem["tone"] = "info") =>
  useToasts.getState().push(text, tone);
