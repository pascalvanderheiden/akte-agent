import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LanguageSelector, LocaleProvider } from "@/components/LocaleProvider";

// Metadata URLs are not basePath-prefixed by Next, so do it here.
const asset = (path: string) =>
  `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}${path}`;

export const metadata: Metadata = {
  title: "Akte Agent",
  description:
    "Enterprise AI Agent powered by GitHub Copilot SDK & Microsoft Foundry",
  icons: {
    icon: [
      { url: asset("/favicon.svg"), type: "image/svg+xml" },
      { url: asset("/favicon-32x32.png"), sizes: "32x32", type: "image/png" },
      { url: asset("/favicon-16x16.png"), sizes: "16x16", type: "image/png" },
    ],
    apple: [{ url: asset("/apple-touch-icon.png"), sizes: "180x180" }],
  },
  manifest: asset("/manifest.webmanifest"),
};

export const viewport: Viewport = {
  themeColor: "#1a2b58",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-surface antialiased font-sans transition-colors duration-200">
        <LocaleProvider>
          <ThemeProvider><LanguageSelector />{children}</ThemeProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
