// The page's side panel (the burger menu) and its small modal (the setup key),
// built from the shared Sheet and Dialog parts with a lighter overlay than their
// defaults, the page theme on the portal, and no slide-in for reduced motion.

import type { ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { DialogOverlay, DialogPortal } from "@/components/ui/dialog";
import { Sheet, SheetOverlay, SheetPortal } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { THEME_CLASS } from "./theme";

const OVERLAY = "bg-stone-900/30 motion-reduce:!animate-none";

function CloseButton() {
  return (
    <DialogPrimitive.Close
      className="absolute right-3 top-3 grid h-11 w-11 place-items-center rounded-full text-muted-foreground hover:bg-stone-100 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      aria-label="Close"
    >
      <X className="h-5 w-5" />
    </DialogPrimitive.Close>
  );
}

export function SidePanel({
  open,
  onOpenChange,
  title,
  description,
  children,
  testId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetPortal>
        <SheetOverlay className={OVERLAY} />
        <DialogPrimitive.Content
          data-testid={testId}
          className={cn(
            THEME_CLASS,
            "fixed inset-y-0 right-0 z-50 flex h-full w-[92vw] max-w-md flex-col bg-background shadow-xl outline-none",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right data-[state=open]:duration-300 data-[state=closed]:duration-200",
            "motion-reduce:!animate-none",
          )}
        >
          <div className="border-b border-stone-200/80 px-5 pb-3 pt-4 pr-16">
            <DialogPrimitive.Title className="text-xl font-semibold">{title}</DialogPrimitive.Title>
            {description ? (
              <DialogPrimitive.Description className="mt-0.5 text-sm text-muted-foreground">{description}</DialogPrimitive.Description>
            ) : (
              <DialogPrimitive.Description className="sr-only">Settings and details</DialogPrimitive.Description>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">{children}</div>
          <CloseButton />
        </DialogPrimitive.Content>
      </SheetPortal>
    </Sheet>
  );
}

/** A small sheet of actions: from the bottom on a phone, a centred dialog on a desktop (like the pickers). */
export function ActionSheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  testId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className={OVERLAY} />
        <DialogPrimitive.Content
          data-testid={testId}
          className={cn(
            THEME_CLASS,
            "fixed inset-x-0 bottom-0 z-50 max-h-[88dvh] overflow-y-auto rounded-t-2xl bg-background px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 shadow-xl outline-none",
            "sm:inset-x-auto sm:bottom-auto sm:left-1/2 sm:top-[18vh] sm:w-[min(28rem,calc(100vw-3rem))] sm:-translate-x-1/2 sm:rounded-2xl sm:p-6",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0",
            "data-[state=open]:slide-in-from-bottom-8 sm:data-[state=open]:slide-in-from-bottom-2 motion-reduce:!animate-none",
          )}
        >
          <div className="mx-auto mb-2 h-1.5 w-10 rounded-full bg-stone-300 sm:hidden" aria-hidden />
          <DialogPrimitive.Title className="pr-12 text-xl font-semibold leading-snug">{title}</DialogPrimitive.Title>
          {description ? (
            <DialogPrimitive.Description className="mt-1 text-[15px] leading-snug text-muted-foreground">{description}</DialogPrimitive.Description>
          ) : (
            <DialogPrimitive.Description className="sr-only">Actions</DialogPrimitive.Description>
          )}
          <div className="mt-3">{children}</div>
          <CloseButton />
        </DialogPrimitive.Content>
      </DialogPortal>
    </DialogPrimitive.Root>
  );
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className={OVERLAY} />
        <DialogPrimitive.Content
          className={cn(
            THEME_CLASS,
            "fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 rounded-2xl bg-background p-5 shadow-xl outline-none sm:top-[18vh] sm:p-6",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95",
            "motion-reduce:!animate-none",
          )}
        >
          <DialogPrimitive.Title className="pr-10 text-xl font-semibold">{title}</DialogPrimitive.Title>
          {description ? (
            <DialogPrimitive.Description className="mt-1 text-[15px] leading-relaxed text-muted-foreground">
              {description}
            </DialogPrimitive.Description>
          ) : (
            <DialogPrimitive.Description className="sr-only">Dialog</DialogPrimitive.Description>
          )}
          <div className="mt-4">{children}</div>
          <CloseButton />
        </DialogPrimitive.Content>
      </DialogPortal>
    </DialogPrimitive.Root>
  );
}
