import type { Metadata } from "next";
import { NavBar } from "../components/NavBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "CognitiveOS Workspace",
  description: "Collaborative workspace and governance surface for CognitiveOS actors.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <NavBar />
        {children}
      </body>
    </html>
  );
}
