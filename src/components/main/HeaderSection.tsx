import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import Header from '@/components/Header';
import { Button } from '@/components/ui/button';
import { 
  Terminal, 
  Menu, 
  Info, 
  Clock, 
  StopCircle, 
  Monitor, 
  Settings,
  Edit,
  FileJson,
  Sparkles
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownMenuPortal
} from '@/components/ui/dropdown-menu';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

interface HeaderSectionProps {
  onToggleConsole: () => void;
  isConsoleVisible: boolean;
  onOpenAdvancedOptions?: () => void;
  onOpenAboutDialog?: () => void;
}

const HeaderSection: React.FC<HeaderSectionProps> = ({ 
  onToggleConsole,
  isConsoleVisible,
  onOpenAboutDialog
}) => {
  const { destinations } = usePublishDestinations();

  const handleCancelAllJobs = async () => {
    toast.loading("Cancelling all jobs...");
    try {
      const result = await apiService.cancelAllJobs();
      if (result.success) {
        toast.success(`Cancelled ${result.cancelled} jobs`);
      } else {
        toast.error(`Failed to cancel jobs: ${result.error}`);
      }
    } catch (error) {
      console.error("Error cancelling jobs:", error);
      toast.error("Failed to cancel jobs due to an unexpected error");
    }
  };

  return (
    <div className="flex justify-between items-center">
      <Header onOpenAboutDialog={onOpenAboutDialog} />
      
      <div className="flex items-center space-x-1">
        {/* Only show console button outside the menu if visible */}
        {isConsoleVisible && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                variant="ghost" 
                size="icon" 
                onClick={onToggleConsole}
                className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50 mr-2"
              >
                <Terminal className="h-5 w-5 text-primary" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Toggle Command Console</p>
            </TooltipContent>
          </Tooltip>
        )}
        
        {/* Burger menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button 
              variant="ghost" 
              size="icon" 
              className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            {/* 0. Generate */}
            <DropdownMenuItem asChild>
              <Link to="/">
                <Sparkles className="h-4 w-4 mr-2" />
                <span>Generate</span>
              </Link>
            </DropdownMenuItem>
            
            {/* 1. Scheduler */}
            <DropdownMenuItem asChild>
              <Link to="/scheduler" target="_blank" rel="noopener noreferrer">
                <Clock className="h-4 w-4 mr-2" />
                <span>Scheduler</span>
              </Link>
            </DropdownMenuItem>
            
            {/* 2. Displays sub-menu */}
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Monitor className="h-4 w-4 mr-2" />
                <span>Displays</span>
              </DropdownMenuSubTrigger>
              <DropdownMenuPortal>
                <DropdownMenuSubContent>
                  {destinations.length > 0 ? (
                    destinations.map(dest => (
                      <DropdownMenuItem key={dest.id} asChild>
                        <Link to={`/display/${dest.id}`} target="_blank" rel="noopener noreferrer">
                          <span>{dest.name || dest.id}</span>
                        </Link>
                      </DropdownMenuItem>
                    ))
                  ) : (
                    <DropdownMenuItem disabled>
                      <span>No displays found</span>
                    </DropdownMenuItem>
                  )}
                </DropdownMenuSubContent>
              </DropdownMenuPortal>
            </DropdownMenuSub>
            
            {/* 3. Manage sub-menu */}
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Settings className="h-4 w-4 mr-2" />
                <span>Manage</span>
              </DropdownMenuSubTrigger>
              <DropdownMenuPortal>
                <DropdownMenuSubContent>
                  <DropdownMenuItem onClick={handleCancelAllJobs}>
                    <StopCircle className="h-4 w-4 mr-2" />
                    <span>Cancel All Jobs</span>
                  </DropdownMenuItem>
                  
                  <DropdownMenuItem disabled>
                    <Edit className="h-4 w-4 mr-2" />
                    <span>Edit Displays</span>
                  </DropdownMenuItem>
                  
                  <DropdownMenuItem disabled>
                    <FileJson className="h-4 w-4 mr-2" />
                    <span>Edit Workflows</span>
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuPortal>
            </DropdownMenuSub>
            
            {/* 4. Console (if not visible) */}
            {!isConsoleVisible && (
              <DropdownMenuItem onClick={onToggleConsole}>
                <Terminal className="h-4 w-4 mr-2" />
                <span>Console View</span>
              </DropdownMenuItem>
            )}
            
            {/* 5. About */}
            <DropdownMenuItem onClick={onOpenAboutDialog}>
              <Info className="h-4 w-4 mr-2" />
              <span>About</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
};

export default HeaderSection; 