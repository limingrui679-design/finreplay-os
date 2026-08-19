import type { Metadata } from "next";
import "./globals.css";

const title = "FinReplay OS · Evidence before confidence";
const description = "A public read-only view of point-in-time replay evidence, thirty boundary cases, billion-row scale, and explicit claim limits.";

export const metadata: Metadata = {
  metadataBase: new URL("https://finreplay-evidence.limingrui2.chatgpt.site"),
  title,
  description,
  openGraph: {
    type: "website",
    title,
    description,
    images: [{ url: "/og.png", width: 1731, height: 909, alt: "FinReplay OS public read-only evidence" }],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
