import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LanguageSelector, LocaleProvider } from "@/components/LocaleProvider";

export const metadata: Metadata = {
  title: "Kratos Agent",
  description:
    "Enterprise AI Agent powered by GitHub Copilot SDK & Microsoft Foundry",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
      </head>
      <body className="min-h-screen bg-surface antialiased font-sans transition-colors duration-200">
        <LocaleProvider>
          <ThemeProvider><LanguageSelector />{children}</ThemeProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
