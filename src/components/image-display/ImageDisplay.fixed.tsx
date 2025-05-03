import React, { useState, useEffect, useMemo, useRef } from 'react';
import FullscreenDialog from './FullscreenDialog';
import ViewModeContent from './ViewModeContent';
import useImageDisplayState from './hooks/useImageDisplayState';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';
import * as LucideIcons from 'lucide-react';
import { BucketGridView } from './BucketGridView';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import ViewModeSelector from './ViewModeSelector';
import { CirclePause, CirclePlay, CircleStop, Settings, Image, ImagePlus, ChevronRight, ChevronLeft } from 'lucide-react';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

// ... existing types ...

export function ImageDisplay(props: ImageDisplayProps) {
  const { destinationsWithBuckets, loading: destinationsLoading } = usePublishDestinations();
  const [selectedTab, setSelectedTab] = useState<string>('generated');
  const [bucketRefreshFlags, setBucketRefreshFlags] = useState<Record<string, number>>({});
  const [viewMode, setViewMode] = useState<ViewMode>('normal');
  const [sortField, setSortField] = useState<SortField>('timestamp');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
  const [schedulerStatuses, setSchedulerStatuses] = useState<Record<string, any>>({});

  // Convert array of destinations to tabs format
  const destinationTabs = useMemo(() => {
    // Start with the Generated tab
    const tabs = [{
      id: 'generated',
      label: 'Generated',
      icon: <ImagePlus className="h-4 w-4 mr-2" />,
      highlight: true,
      file: null
    }];

    // Add tabs for each destination that has a bucket
    destinationsWithBuckets.forEach(dest => {
      if (dest.has_bucket) {
        tabs.push({
          id: dest.id,
          label: dest.name,
          icon: <Image className="h-4 w-4 mr-2" />,
          highlight: false,
          file: dest.file || dest.id,
          headless: dest.headless || false
        });
      }
    });

    return tabs;
  }, [destinationsWithBuckets]);

  // ... rest of the component code ...
} 