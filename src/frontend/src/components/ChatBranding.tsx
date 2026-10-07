import { BrandLogo } from "./BrandLogo";

interface LandingHeroBrandingProps {
  displayName?: string;
  description: string;
  productName: string;
}

export function LandingHeroBranding({ displayName, description, productName }: LandingHeroBrandingProps) {
  const title = displayName || productName;

  return (
    <div className="text-center mb-10">
      <div className="mx-auto mb-6 flex justify-center">
        <BrandLogo variant="full" height={160} decorative={title === productName} />
      </div>

      <h1 className="text-3xl sm:text-4xl font-bold mb-3 tracking-tight">
        <span className="gradient-text">{title}</span>
      </h1>
      <p className="text-muted text-sm sm:text-base leading-relaxed max-w-lg mx-auto">
        {description}
      </p>
    </div>
  );
}

interface MobileChatHeaderProps {
  openSidebar: () => void;
  openSidebarLabel: string;
}

export function MobileChatHeader({ openSidebar, openSidebarLabel }: MobileChatHeaderProps) {
  return (
    <div className="lg:hidden flex items-center px-4 py-3 border-b border-border-soft bg-surface backdrop-blur-sm">
      <button
        onClick={openSidebar}
        aria-label={openSidebarLabel}
        className="shrink-0 p-2 -ml-1 text-muted hover:text-text rounded-lg hover:bg-hover transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
        </svg>
      </button>
      <BrandLogo variant="mark" height={36} className="ml-2" />
    </div>
  );
}
