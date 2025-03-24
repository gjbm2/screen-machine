import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { DisplayParams, ShowMode, PositionMode, CaptionPosition, TransitionType } from '../types';
import { createUrlWithParams, getDefaultParams } from '../utils';

interface DebugPanelHookProps {
  params: DisplayParams;
  imageUrl: string | null;
  metadata: Record<string, string>;
  onApplyCaption: (caption: string | null) => void;
}

export const useDebugPanel = ({ params, imageUrl, metadata, onApplyCaption }: DebugPanelHookProps) => {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState("files");
  const [customUrl, setCustomUrl] = useState(params.output || "");
  const [showMode, setShowMode] = useState<ShowMode>(params.showMode);
  const [position, setPosition] = useState<PositionMode>(params.position);
  const [refreshInterval, setRefreshInterval] = useState(params.refreshInterval);
  const [backgroundColor, setBackgroundColor] = useState(params.backgroundColor);
  const [caption, setCaption] = useState(params.caption || "");
  const [captionPosition, setCaptionPosition] = useState<CaptionPosition>(params.captionPosition || "bottom-center");
  const [captionSize, setCaptionSize] = useState(params.captionSize || "16px");
  const [captionColor, setCaptionColor] = useState(params.captionColor || "ffffff");
  const [captionFont, setCaptionFont] = useState(params.captionFont || "Arial, sans-serif");
  const [captionBgColor, setCaptionBgColor] = useState(params.captionBgColor || "#000000");
  const [captionBgOpacity, setCaptionBgOpacity] = useState(params.captionBgOpacity || 0.7);
  const [transition, setTransition] = useState<TransitionType>(params.transition || "cut");
  const [copied, setCopied] = useState(false);
  const [metadataEntries, setMetadataEntries] = useState<Array<{key: string, value: string}>>([]);
  const [previewCaption, setPreviewCaption] = useState<string | null>(null);
  
  const [position2, setPosition2] = useState({ x: 4, y: 4 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const panelRef = useRef<HTMLDivElement>(null);
  
  const [isResizing, setIsResizing] = useState(false);
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, width: 0, height: 0 });
  const [panelSize, setPanelSize] = useState({ width: '480px', height: 'auto' });
  
  const previousImageUrlRef = useRef<string | null>(null);

  const defaultParams = getDefaultParams();

  useEffect(() => {
    if (imageUrl !== previousImageUrlRef.current) {
      console.log("Image URL changed, updating metadata entries");
      previousImageUrlRef.current = imageUrl;
      
      const entries = Object.entries(metadata).map(([key, value]) => ({
        key,
        value
      }));
      setMetadataEntries(entries);
    }
  }, [metadata, imageUrl]);

  useEffect(() => {
    if (caption === '{all}') {
      const allMetadata = metadataEntries
        .map(entry => `${entry.key}: ${entry.value}`)
        .join('\n');
      setPreviewCaption(allMetadata);
      onApplyCaption(allMetadata);
    } else if (caption) {
      const processed = caption.replace(/\{([^}]+)\}/g, (match, key) => {
        const entry = metadataEntries.find(e => e.key === key);
        return entry ? entry.value : match;
      });
      setPreviewCaption(processed);
      onApplyCaption(processed);
    } else {
      setPreviewCaption(null);
      onApplyCaption(null);
    }
  }, [caption, metadataEntries, onApplyCaption]);

  useEffect(() => {
    if (imageUrl) {
      onApplyCaption(previewCaption);
    }
  }, [
    showMode, 
    position, 
    backgroundColor, 
    captionPosition, 
    captionSize, 
    captionColor, 
    captionFont, 
    captionBgColor, 
    captionBgOpacity, 
    previewCaption,
    onApplyCaption,
    imageUrl
  ]);

  const generateUrl = (includeDebug = false) => {
    const encodedOutput = customUrl ? encodeURIComponent(customUrl) : null;
    
    const newParams: DisplayParams = {
      output: encodedOutput,
      showMode,
      position,
      refreshInterval,
      backgroundColor,
      debugMode: includeDebug,
      caption: caption || null,
      captionPosition,
      captionSize,
      captionColor,
      captionFont,
      captionBgColor,
      captionBgOpacity,
      data: params.data,
      transition,
    };
    
    const cleanParams = Object.entries(newParams).reduce((acc, [key, value]) => {
      if (key === 'debugMode' && includeDebug) {
        acc[key] = value;
      } else if (key === 'output') {
        if (value !== null) acc[key] = value;
      } else if (key !== 'debugMode' && value !== null && value !== undefined) {
        if (String(value) !== String(defaultParams[key])) {
          acc[key] = value;
        }
      }
      return acc;
    }, {} as Partial<DisplayParams>);
    
    return createUrlWithParams(cleanParams as DisplayParams);
  };

  const applySettings = () => {
    const encodedOutput = customUrl ? encodeURIComponent(customUrl) : null;
    
    const newParams: DisplayParams = {
      output: encodedOutput,
      showMode,
      position,
      refreshInterval,
      backgroundColor,
      debugMode: true,
      caption: caption || null,
      captionPosition,
      captionSize,
      captionColor,
      captionFont,
      captionBgColor,
      captionBgOpacity,
      data: params.data,
      transition,
    };
    
    const url = createUrlWithParams(newParams);
    navigate(url);
    toast.success("Settings applied");
  };

  const resetDisplay = () => {
    setCustomUrl("");
    resetSettings();
    navigate('/display');
    toast.success("Display reset to defaults");
  };

  const commitSettings = () => {
    const url = generateUrl(false);
    navigate(url);
    toast.success("Settings committed");
  };

  const copyUrl = () => {
    const url = window.location.origin + generateUrl(false);
    navigator.clipboard.writeText(url)
      .then(() => {
        setCopied(true);
        toast.success("URL copied to clipboard");
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(err => {
        console.error('Failed to copy URL: ', err);
        toast.error("Failed to copy URL");
      });
  };

  const selectFile = (file: string) => {
    setCustomUrl(file);
    applySettings();
  };

  const formatFileName = (file: string) => {
    if (file.startsWith('http')) {
      try {
        const url = new URL(file);
        return url.pathname.split('/').pop() || file;
      } catch (e) {
        return file;
      }
    }
    return file;
  };

  const resetSettings = () => {
    setShowMode(defaultParams.showMode);
    setPosition(defaultParams.position);
    setRefreshInterval(defaultParams.refreshInterval);
    setBackgroundColor(defaultParams.backgroundColor);
    setCaption('');
    setCaptionPosition(defaultParams.captionPosition);
    setCaptionSize(defaultParams.captionSize);
    setCaptionColor(defaultParams.captionColor);
    setCaptionFont(defaultParams.captionFont);
    setCaptionBgColor(defaultParams.captionBgColor);
    setCaptionBgOpacity(defaultParams.captionBgOpacity);
    setTransition(defaultParams.transition);
  };

  const insertMetadataTag = (key: string) => {
    setCaption(prevCaption => {
      const textArea = document.getElementById('caption-textarea') as HTMLTextAreaElement;
      if (textArea) {
        const selectionStart = textArea.selectionStart;
        const selectionEnd = textArea.selectionEnd;
        const textBefore = prevCaption.substring(0, selectionStart);
        const textAfter = prevCaption.substring(selectionEnd);
        const newCaption = `${textBefore}{${key}}${textAfter}`;
        
        setTimeout(() => {
          textArea.focus();
          const newPosition = selectionStart + key.length + 2;
          textArea.setSelectionRange(newPosition, newPosition);
        }, 50);
        
        return newCaption;
      }
      return `${prevCaption}{${key}}`;
    });
  };

  const isCurrentFile = (file: string) => {
    if (!imageUrl) return false;
    
    if (imageUrl.startsWith('http')) {
      return imageUrl === file;
    } else {
      const currentFile = imageUrl.split('/').pop();
      const compareFile = file.split('/').pop();
      return currentFile === compareFile;
    }
  };

  const formatTime = (timeValue: Date | string | null) => {
    if (!timeValue) return 'Never';
    
    try {
      const date = timeValue instanceof Date ? timeValue : new Date(timeValue);
      return date.toLocaleTimeString();
    } catch (e) {
      return String(timeValue);
    }
  };

  const insertAllMetadata = () => {
    setCaption('{all}');
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target instanceof Element && e.target.closest('.card-header-drag-handle')) {
      setIsDragging(true);
      const rect = panelRef.current?.getBoundingClientRect();
      if (rect) {
        setDragOffset({
          x: e.clientX - rect.left,
          y: e.clientY - rect.top
        });
      }
    }
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (isDragging) {
      const newX = e.clientX - dragOffset.x;
      const newY = e.clientY - dragOffset.y;
      setPosition2({ x: newX, y: newY });
    }
    
    if (isResizing) {
      const newWidth = Math.max(300, resizeStart.width + (e.clientX - resizeStart.x));
      const newHeight = Math.max(400, resizeStart.height + (e.clientY - resizeStart.y));
      setPanelSize({ 
        width: `${newWidth}px`, 
        height: `${newHeight}px` 
      });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
    setIsResizing(false);
  };

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    setIsResizing(true);
    setResizeStart({
      x: e.clientX,
      y: e.clientY,
      width: panelRef.current?.offsetWidth || 480,
      height: panelRef.current?.offsetHeight || 600
    });
  };

  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset, isResizing, resizeStart]);

  return {
    activeTab,
    setActiveTab,
    customUrl,
    setCustomUrl,
    showMode,
    setShowMode,
    position,
    setPosition,
    refreshInterval, 
    setRefreshInterval,
    backgroundColor,
    setBackgroundColor,
    caption,
    setCaption,
    captionPosition,
    setCaptionPosition,
    captionSize,
    setCaptionSize,
    captionColor,
    setCaptionColor,
    captionFont,
    setCaptionFont,
    captionBgColor,
    setCaptionBgColor,
    captionBgOpacity,
    setCaptionBgOpacity,
    transition,
    setTransition,
    copied,
    metadataEntries,
    previewCaption,
    position2,
    isDragging,
    panelRef,
    panelSize,
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl,
    selectFile,
    formatFileName,
    resetSettings,
    insertMetadataTag,
    isCurrentFile,
    formatTime,
    insertAllMetadata,
    handleMouseDown,
    handleResizeStart
  };
};
