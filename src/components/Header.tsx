
import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

interface HeaderProps {
  onOpenAboutDialog?: () => void;
}

const Header: React.FC<HeaderProps> = ({ onOpenAboutDialog }) => {
  const [open, setOpen] = React.useState(false);

  // Handle external trigger for opening the dialog
  React.useEffect(() => {
    if (onOpenAboutDialog) {
      const handler = () => setOpen(true);
      // Store the reference to the handler function
      onOpenAboutDialog = handler;
      return () => {
        // Cleanup to avoid memory leaks
        onOpenAboutDialog = undefined;
      };
    }
  }, [onOpenAboutDialog]);

  return (
    <header className="flex justify-between items-center py-4 px-4 sm:px-6 md:px-8 animate-fade-in">
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
          <div className="text-xl font-medium tracking-tight cursor-pointer hover:text-primary transition-colors">
            screen/machine
          </div>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>About Imagine</DialogTitle>
            <DialogDescription>
              <div className="space-y-4 mt-4">
                <p>
                  Screen machine imagines what you can't be bothered to.
                </p>
                <p>
                  Describe what you want to see or upload reference images. Let AI do the rest.
                </p>
                <p className="text-xs text-muted-foreground">
                  Version 1.0.0. Greg Marsh.
                </p>
              </div>
            </DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    </header>
  );
};

export default Header;
