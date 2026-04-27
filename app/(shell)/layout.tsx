"use client";
import { App } from "@/components/app-shell";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <App />
      {children}
    </>
  );
}
