import React, { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import PromptForm from '@/components/prompt-form/PromptForm';
import IntroText from '@/components/IntroText';
import MainLayout from '@/components/layout/MainLayout';
import AdvancedOptionsContainer from '@/components/advanced/AdvancedOptionsContainer';
import { useImageGeneration } from '@/hooks/image-generation/use-image-generation';
import { useConsoleManagement } from '@/hooks/use-console-management';
import { WebSocketMessage, AsyncGenerationUpdate, GeneratedImage } from '@/hooks/image-generation/types';
import { nanoid } from '@/lib/utils';
import typedWorkflows from '@/data/typedWorkflows';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';
import { processGenerationResults, markBatchAsError } from '@/hooks/image-generation/api/result-handler';
import {
  DndContext,
  useSensor,
  useSensors,
  MouseSensor,
  TouchSensor,
  KeyboardSensor,
  closestCenter,
} from '@dnd-kit/core';
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { DROP_ZONES } from '@/dnd/dropZones';
import { getReferenceUrl } from '@/utils/image-utils';
import { useUploadedImages } from '@/hooks/image-generation/use-uploaded-images';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';
import { useReferenceImagesAdapter } from '@/hooks/context-adapter';

interface OverlayMessage {
  html: string;
  duration: number;
  position?: string;
  clear?: boolean;
  screens?: string[];
  fadein?: number;
}

interface Overlay {
  id: string;
  html: string;
  position: string | undefined;
  visible: boolean;
  fadein?: number;
}

interface JobStatusMessage {
  screens?: string | string[];
  html: string;
  duration?: number;
  position?: string;
  clear?: boolean;
  fadein?: number;
  job_id: string;
  substitutions?: Record<string, any>;
}

// Enhanced JobStatus interface to track parsed message details
interface JobStatus {
  id: string;
  html: string;
  visible: boolean;
  messageHash: string;
  timeoutId?: ReturnType<typeof setTimeout>;
  destination?: string;
  progress?: number;
  message?: string;
  startTime: number;
}

// Parse the backend message format: "destination: progress% - message"
function parseJobMessage(html: string): { destination?: string; progress?: number; message?: string } {
  if (!html || typeof html !== 'string') {
    return { message: html || '' };
  }
  
  console.log('🔍 Parsing job message:', html);
  
  // Try to match the format: "destination: progress% - message"
  const progressMatch = html.match(/^([^:]+):\s*(\d+)%\s*-\s*(.+)$/);
  if (progressMatch) {
    const result = {
      destination: progressMatch[1].trim(),
      progress: parseInt(progressMatch[2]),
      message: progressMatch[3].trim()
    };
    console.log('✅ Progress format matched:', result);
    return result;
  }
  
  // Try to match just "destination: message" (no progress)
  const destinationMatch = html.match(/^([^:]+):\s*(.+)$/);
  if (destinationMatch) {
    const result = {
      destination: destinationMatch[1].trim(),
      message: destinationMatch[2].trim()
    };
    console.log('✅ Destination format matched:', result);
    return result;
  }
  
  // If no pattern matches, return the raw message
  const result = { message: html };
  console.log('❌ No pattern matched, using raw message:', result);
  return result;
}

function generateId(): string {
  return (typeof crypto !== "undefined" && crypto.randomUUID)
    ? crypto.randomUUID()
    : Math.random().toString(36).substring(2, 10);
}

function hashMessage(message: JobStatusMessage): string {
  return `${message.html}_${message.screens}_${message.position}`;
}

const Index = () => {
  const [advancedOptionsOpen, setAdvancedOptionsOpen] = useState(false);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const sessionId = React.useMemo(() => nanoid(), []);
  const [overlays, setOverlays] = useState<Overlay[]>([]);
  const [jobStatuses, setJobStatuses] = useState<Record<string, JobStatus>>({});
  const [wsConnected, setWsConnected] = useState(false);
  const [statusPanelExpanded, setStatusPanelExpanded] = useState(true);
  const [publishDestinations, setPublishDestinations] = useState<PublishDestination[]>([]);
  const wsRef = React.useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  
  const { 
    consoleVisible, 
    consoleLogs, 
    toggleConsole, 
    clearConsole,
    addLog: addConsoleLog 
  } = useConsoleManagement();
  
  const [selectedRefiner, setSelectedRefiner] = useState('auto');
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>({});
  const [selectedPublish, setSelectedPublish] = useState('none');

  const {
    generatedImages,
    activeGenerations,
    imageUrl,
    currentPrompt,
    uploadedImageUrls,
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    imageContainerOrder,
    expandedContainers,
    lastBatchId,
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentPrompt,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setCurrentParams,
    setCurrentGlobalParams,
    setImageContainerOrder,
    setExpandedContainers,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
    handleDeleteContainer,
    setGeneratedImages,
    removeUrl
  } = useImageGeneration(addConsoleLog);
  
  const { addReferenceUrl, referenceUrls } = useReferenceImages();
  
  const handleJobStatusMessage = useCallback((message: JobStatusMessage) => {
    console.log('🔍 handleJobStatusMessage called with:', message);
    
    if (!message || !message.job_id) {
      console.log('❌ Invalid message: missing job_id');
      return;
    }
    
    const screens = message.screens || "";
    const isForThisScreen = 
      !message.screens || 
      message.screens === 'index' || 
      (Array.isArray(message.screens) && 
       (message.screens.includes('index') || message.screens.includes('*')));

    console.log('🎯 Screen check:', { screens, isForThisScreen });

    if (!isForThisScreen) {
      console.log('❌ Message not for this screen, ignoring');
      return;
    }

    const jobId = message.job_id;
    
    // Extract the actual message from the HTML template
    let messageText = 'Processing...';
    let progressPercent = null;
    
    if (message.html) {
      // If it's a simple string (not HTML template), use it directly
      if (!message.html.includes('{{MESSAGE}}') && !message.html.includes('<div')) {
        messageText = message.html;
      } else {
        // Extract message from substitutions if available
        if (message.substitutions && message.substitutions.MESSAGE) {
          messageText = message.substitutions.MESSAGE;
        }
        
        // Extract progress from substitutions if available
        if (message.substitutions && message.substitutions.PROGRESS_PERCENT) {
          progressPercent = parseInt(message.substitutions.PROGRESS_PERCENT);
        }
      }
    }
    
    console.log('📝 Job message:', { jobId, messageText, progressPercent });

    setJobStatuses(prev => {
      // Clear any existing timeout for this job
      if (prev[jobId]?.timeoutId) {
        clearTimeout(prev[jobId].timeoutId);
      }
      
      // Create new status
      const updatedStatus: JobStatus = {
        id: jobId,
        html: messageText,
        visible: true,
        messageHash: hashMessage(message),
        timeoutId: undefined as ReturnType<typeof setTimeout> | undefined,
        destination: undefined,
        progress: progressPercent,
        message: messageText,
        startTime: prev[jobId]?.startTime || Date.now()
      };
      
      // Set timeout to hide this message after 10 seconds
      updatedStatus.timeoutId = setTimeout(() => {
        console.log('⏰ Hiding job message:', jobId);
        setJobStatuses(currentState => {
          const newState = { ...currentState };
          delete newState[jobId];
          return newState;
        });
      }, 10000); // Show for 10 seconds
      
      const newState = {
        ...prev,
        [jobId]: updatedStatus
      };
      
      console.log('📊 Updated job status:', updatedStatus);
      return newState;
    });
  }, []);

  const handleCloseJobStatus = useCallback((jobId: string) => {
    setJobStatuses(prev => {
      if (prev[jobId]?.timeoutId) {
        clearTimeout(prev[jobId].timeoutId);
      }
      
      if (!prev[jobId]) return prev;
      
      return {
        ...prev,
        [jobId]: {
          ...prev[jobId],
          visible: false,
          timeoutId: undefined
        }
      };
    });
  }, []);
  
  const handleGenerationUpdate = useCallback((update: AsyncGenerationUpdate) => {
    if (update.status === 'progress' && update.progress !== undefined) {
      toast.info(`Generation progress: ${update.progress}%`);
    } else if (update.status === 'completed') {
      if (update.images && update.images.length > 0) {
        toast.success(`Completed async generation with ${update.images.length} images`);
        setGeneratedImages(prevImages => {
          const updatedImages = processGenerationResults(update, update.batch_id, prevImages);
          return updatedImages;
        });
      }
    } else if (update.status === 'error') {
      toast.error(`Generation failed: ${update.error || 'Unknown error'}`);
      setGeneratedImages(prevImages => {
        return markBatchAsError(update.batch_id, prevImages);
      });
    }
  }, [addConsoleLog, setGeneratedImages]);

  const handleWebSocketMessage = useCallback((event: MessageEvent) => {
    try {
      const msg = JSON.parse(event.data);
      
      // Filter out light sensor messages from debug logging to reduce noise
      if (!msg.sensor_name) {
        console.log('📬 Raw WebSocket message received:', event.data);
        console.log('📬 Parsed WebSocket message:', msg);
      }
      
      // Skip light sensor messages entirely
      if (msg.sensor_name) {
        return;
      }
      
      // Check if this is a job status message (has job_id) - but NOT ComfyUI progress messages
      if (msg.job_id && !msg.comfy) {
        console.log('🔍 Processing job status message with job_id:', msg.job_id);
        handleJobStatusMessage(msg);
        return;
      }
      
      // Skip ComfyUI progress messages - these come from the remote serverless unit
      if (msg.comfy) {
        console.log('⏭️ Skipping ComfyUI progress message:', msg.comfy.type);
        return;
      }
      
      if (msg.type === 'generation_update') {
        handleGenerationUpdate(msg);
        return;
      }
      
      // Handle regular overlay messages - check if it's a generation progress message
      if (msg.html && msg.substitutions) {
        const screens = msg.screens || null;
        const isForThisScreen = 
          !screens || 
          screens === 'index' || 
          (Array.isArray(screens) && 
           (screens.includes('index') || screens.includes('*')));
        
        if (isForThisScreen) {
          // Check if this is a generation progress message (has PROGRESS_PERCENT)
          if (msg.substitutions.PROGRESS_PERCENT !== undefined) {
            const progressPercent = msg.substitutions.PROGRESS_PERCENT;
            const messageText = msg.substitutions.MESSAGE || 'Processing...';
            const pubDest = Array.isArray(msg.screens) ? msg.screens[0] : msg.screens;
            
            // Use pub-dest as the key for deduplication
            const fakeJobMessage = {
              job_id: pubDest, // Use pub-dest as the key for deduplication
              html: `${pubDest}: ${progressPercent}% - ${messageText}`,
              screens: 'index',
              duration: msg.duration || 10000
            };
            
            console.log('🔍 Converting overlay to job status (keyed by pub-dest):', fakeJobMessage);
            handleJobStatusMessage(fakeJobMessage);
            return;
          }
          
          // Regular overlay message - use old system
          const overlay: Overlay = {
            id: generateId(),
            html: msg.html,
            position: msg.position,
            visible: true,
            fadein: msg.fadein
          };
          
          setOverlays(prev => [...prev, overlay]);
          
          if (msg.duration) {
            setTimeout(() => {
              setOverlays(prev => prev.filter(o => o.id !== overlay.id));
            }, msg.duration);
          }
        }
        return;
      }
    } catch (error) {
      console.error("Error parsing WebSocket message:", error);
    }
  }, [handleJobStatusMessage, handleGenerationUpdate]);
  
  useEffect(() => {
    const connect = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        return;
      }

      const ws = new WebSocket(import.meta.env.VITE_WS_HOST || 'ws://185.254.136.253:8765');
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('WebSocket connection established');
        setWsConnected(true);
        addConsoleLog({
          type: 'success',
          message: 'WebSocket connection established'
        });
        
        // Clear any existing reconnect timeout
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
      };

      ws.onclose = () => {
        console.log('WebSocket connection closed');
        setWsConnected(false);
        addConsoleLog({
          type: 'error',
          message: 'WebSocket connection closed'
        });

        // Attempt to reconnect after a delay
        if (!reconnectTimeoutRef.current) {
          reconnectTimeoutRef.current = setTimeout(() => {
            console.log('Attempting to reconnect...');
            connect();
          }, 5000); // 5 second delay before reconnecting
        }
      };

      ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        addConsoleLog({
          type: 'error',
          message: 'WebSocket error occurred'
        });
      };

      ws.onmessage = handleWebSocketMessage;
    };

    connect();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };
  }, [handleWebSocketMessage, addConsoleLog]);

  const sendWebSocketMessage = useCallback((message: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    } else {
      console.warn('WebSocket is not connected');
      addConsoleLog({
        type: 'warning',
        message: 'Cannot send message: WebSocket is not connected'
      });
    }
  }, [addConsoleLog]);

  const processUrlParam = (value: string): any => {
    if ((value.startsWith('"') && value.endsWith('"')) || 
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    
    try {
      if (value.startsWith('[') || value.startsWith('{')) {
        return JSON.parse(value);
      } else if (value === 'true') {
        return true;
      } else if (value === 'false') {
        return false;
      } else if (!isNaN(Number(value))) {
        return Number(value);
      } else {
        return value;
      }
    } catch (e) {
      console.error(`Failed to parse param value=${value}`, e);
      return value;
    }
  };

  useEffect(() => {
    const promptParam = searchParams.get('prompt');
    if (promptParam) {
      const processedPrompt = processUrlParam(promptParam);
      setCurrentPrompt(processedPrompt);
      addConsoleLog({
        type: 'info',
        message: `URL parameter: prompt=${processedPrompt}`
      });
    }
    
    const workflowParam = searchParams.get('workflow');
    if (workflowParam) {
      const workflowExists = typedWorkflows.some(w => w.id === workflowParam);
      if (workflowExists) {
        setCurrentWorkflow(workflowParam);
        addConsoleLog({
          type: 'info',
          message: `URL parameter: workflow=${workflowParam}`
        });
      } else {
        toast.error(`Workflow '${workflowParam}' not found`);
      }
    }
    
    const publishParam = searchParams.get('publish');
    if (publishParam) {
      setSelectedPublish(publishParam);
      
      addConsoleLog({
        type: 'info',
        message: `URL parameter: publish=${publishParam}`
      });
    }
    
    const refinerParam = searchParams.get('refiner');
    if (refinerParam) {
      setSelectedRefiner(refinerParam);
      addConsoleLog({
        type: 'info',
        message: `URL parameter: refiner=${refinerParam}`
      });
    }
    
    const workflowParamsObj: Record<string, any> = {};
    searchParams.forEach((value, key) => {
      if (!['prompt', 'workflow', 'refiner', 'publish', 'script', 'run'].includes(key) && !key.startsWith('refiner_')) {
        try {
          workflowParamsObj[key] = processUrlParam(value);
          
          addConsoleLog({
            type: 'info',
            message: `URL parameter: ${key}=${value} (workflow param)`
          });
        } catch (e) {
          workflowParamsObj[key] = value;
          console.error(`Failed to parse param ${key}=${value}`, e);
        }
      }
    });
    
    if (Object.keys(workflowParamsObj).length > 0) {
      setCurrentParams(prev => ({
        ...prev,
        ...workflowParamsObj
      }));
    }
    
    const refinerParamsObj: Record<string, any> = {};
    searchParams.forEach((value, key) => {
      if (key.startsWith('refiner_')) {
        const paramName = key.replace('refiner_', '');
        try {
          refinerParamsObj[paramName] = processUrlParam(value);
          
          addConsoleLog({
            type: 'info',
            message: `URL parameter: ${key}=${value} (refiner param)`
          });
        } catch (e) {
          refinerParamsObj[paramName] = value;
          console.error(`Failed to parse refiner param ${key}=${value}`, e);
        }
      }
    });
    
    if (Object.keys(refinerParamsObj).length > 0) {
      setRefinerParams(prev => ({
        ...prev,
        ...refinerParamsObj
      }));
    }
    
    const scriptParam = searchParams.get('script');
    if (scriptParam) {
      addConsoleLog({
        type: 'info',
        message: `URL parameter: script=${scriptParam} (not implemented yet)`
      });
    }
    
    const hasRunParam = searchParams.get('run');
    if (hasRunParam) {
      const timer = setTimeout(() => {
        const currentPromptValue = promptParam ? processUrlParam(promptParam) : currentPrompt;
        
        if (currentPromptValue || Object.keys(workflowParamsObj).length > 0) {
          addConsoleLog({
            type: 'info',
            message: `URL parameter: run=true (auto-generating)`
          });
          
          handleSubmitPrompt(
            currentPromptValue, 
            undefined, 
            workflowParam || currentWorkflow,
            { ...currentParams, ...workflowParamsObj },
            currentGlobalParams,
            refinerParam || selectedRefiner,
            { ...refinerParams, ...refinerParamsObj },
            publishParam || selectedPublish
          );
          
          if (searchParams.has('run')) {
            const newParams = new URLSearchParams(searchParams);
            newParams.delete('run');
            navigate(`/?${newParams.toString()}`, { replace: true });
          }
        } else {
          toast.error('Cannot auto-generate: No prompt or parameters provided');
        }
      }, 500);
      
      return () => clearTimeout(timer);
    }
  }, [
    searchParams, 
    addConsoleLog, 
    setCurrentPrompt, 
    setCurrentWorkflow, 
    setCurrentParams, 
    setSelectedRefiner, 
    setSelectedPublish, 
    handleSubmitPrompt, 
    currentPrompt, 
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    selectedRefiner, 
    refinerParams, 
    selectedPublish, 
    navigate
  ]);

  const handleOpenAdvancedOptions = useCallback(() => {
    console.log('Opening advanced options panel');
    setAdvancedOptionsOpen(true);
  }, []);

  const handleAdvancedOptionsOpenChange = useCallback((open: boolean) => {
    console.log('Advanced options panel open state changing to:', open);
    setAdvancedOptionsOpen(open);
  }, []);

  const handleRefinerChange = useCallback((refiner: string) => {
    console.log('Index: Refiner changed to:', refiner);
    setSelectedRefiner(refiner);
  }, []);

  const handleRefinerParamChange = useCallback((paramId: string, value: any) => {
    console.log('Index: Refiner param changed:', paramId, value);
    setRefinerParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  }, []);

  const handlePublishChange = useCallback((publishId: string) => {
    console.log('Index: Publish destination changed to:', publishId);
    setSelectedPublish(publishId);
  }, []);

  const handlePromptSubmit = async (
    prompt: string,
    imageFiles?: File[] | string[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    publish?: string
  ) => {
    try {
      console.log('Index: handlePromptSubmit called with:');
      console.log('- prompt:', prompt);
      console.log('- workflow:', workflow);
      console.log('- params:', params);
      console.log('- globalParams:', globalParams);
      console.log('- refiner:', refiner);
      console.log('- refinerParams:', refinerParams);
      console.log('- publish:', publish);

      setCurrentPrompt(prompt);

      if (workflow) {
        setCurrentWorkflow(workflow);
      }

      if (refiner) {
        setSelectedRefiner(refiner);
      }

      if (publish) {
        setSelectedPublish(publish);
      }

      let effectiveParams = params ? { ...params } : { ...currentParams };

      if (publish && publish !== 'none') {
        console.log('Adding publish destination to params:', publish);
        effectiveParams.publish_destination = publish;
      }

      setCurrentParams(effectiveParams);

      if (globalParams) {
        setCurrentGlobalParams(globalParams);
      }

      if (refinerParams) {
        setRefinerParams(refinerParams);
      }

      // Use the imageFiles parameter as-is without combining with referenceUrls
      await handleSubmitPrompt(
        prompt,
        imageFiles,
        workflow,
        effectiveParams,
        globalParams,
        refiner,
        refinerParams
      );
    } catch (error) {
      console.error('Error submitting prompt:', error);
      toast.error('Error generating image');
    }
  };

  useEffect(() => {
    if (typeof window !== 'undefined') {
      (window as any).testIndexMessage = (message: any) => {
        if (!message.screens) {
          message.screens = ['index'];
        } else if (typeof message.screens === 'string' && message.screens !== 'index') {
          message.screens = [message.screens, 'index'];
        } else if (Array.isArray(message.screens) && !message.screens.includes('index')) {
          message.screens.push('index');
        }
        
        console.log("🧪 Testing message handling with:", message);
        const event = { data: JSON.stringify(message) };
        handleWebSocketMessage(event as MessageEvent);
        return "Test message sent to handler (screens includes 'index')";
      };
      
      console.log("🧪 testIndexMessage() function is available in browser console");
      
      return () => {
        delete (window as any).testIndexMessage;
      };
    }
  }, [handleWebSocketMessage]);

  useEffect(() => {
    const isLovableEnvironment = 
      typeof window !== 'undefined' && 
      (window.location.hostname === 'localhost' || 
       window.location.hostname.includes('lovable'));
    
    if (!isLovableEnvironment) {
      console.log("Not in Lovable environment, skipping test message simulation");
      return;
    }
    
    console.log("In Lovable environment, will simulate test messages for async workflows");
    
    const originalHandleSubmitPrompt = handleSubmitPrompt;
    
    const wrappedHandleSubmitPrompt = async (...args: Parameters<typeof handleSubmitPrompt>) => {
      const workflowConfig = typedWorkflows.find(w => w.id === (args[2] || currentWorkflow));
      if (workflowConfig?.async) {
        const mockJobId = nanoid();
        const mockMessage: JobStatusMessage = {
          screens: 'index',
          html: `Generating with ${workflowConfig.name}...`,
          duration: 30000,
          job_id: mockJobId
        };
        
        console.log("Simulating job status message for async workflow:", mockMessage);
        handleJobStatusMessage(mockMessage);
        
        setTimeout(() => {
          const updatedMessage: JobStatusMessage = {
            screens: 'index',
            html: `Processing ${workflowConfig.name} results (30%)...`,
            duration: 30000,
            job_id: mockJobId
          };
          console.log("Simulating job status update:", updatedMessage);
          handleJobStatusMessage(updatedMessage);
        }, 3000);
      }
      
      return await originalHandleSubmitPrompt(...args);
    };
    
    (window as any).originalHandleSubmitPrompt = handleSubmitPrompt;
    (window as any).handleSubmitPrompt = wrappedHandleSubmitPrompt;
    
    return () => {
      if ((window as any).originalHandleSubmitPrompt) {
        (window as any).handleSubmitPrompt = (window as any).originalHandleSubmitPrompt;
        delete (window as any).originalHandleSubmitPrompt;
      }
    };
  }, [handleSubmitPrompt, handleJobStatusMessage, currentWorkflow]);

  const [devJobId, setDevJobId] = useState('');
  const [devHtml, setDevHtml] = useState('Rendering...');

  const simulateWebSocketMessage = () => {
    const isLovableEnvironment = 
      typeof window !== 'undefined' && 
      (window.location.hostname === 'localhost' || 
       window.location.hostname.includes('lovable'));
    
    if (!isLovableEnvironment) {
      console.warn('WebSocket simulation only works in Lovable/localhost');
      return;
    }

    const mockMessage: JobStatusMessage = {
      screens: 'index',
      html: devHtml || 'Rendering...',
      duration: 30000,
      job_id: devJobId || nanoid()
    };

    console.log('Simulating WebSocket message:', mockMessage);
    handleJobStatusMessage(mockMessage);
  };

  // Test function to simulate progress updates
  const simulateProgressUpdate = () => {
    const isLovableEnvironment = 
      typeof window !== 'undefined' && 
      (window.location.hostname === 'localhost' || 
       window.location.hostname.includes('lovable'));
    
    if (!isLovableEnvironment) {
      console.warn('Progress simulation only works in Lovable/localhost');
      return;
    }

    const testJobId = nanoid();
    const destinations = ['north-screen', 'south-screen', 'lobby-tv'];
    const testDestination = destinations[Math.floor(Math.random() * destinations.length)];
    
    // Simulate progress from 0 to 100%
    let progress = 0;
    const interval = setInterval(() => {
      progress += Math.floor(Math.random() * 15) + 5; // Random increment between 5-20%
      
      if (progress >= 100) {
        progress = 100;
        clearInterval(interval);
      }
      
      const stages = ['Initialising', 'Render', 'Interpolate', 'Upscale', 'Finalising'];
      const currentStage = stages[Math.floor((progress / 100) * (stages.length - 1))];
      
      const mockMessage: JobStatusMessage = {
        screens: 'index',
        html: `${testDestination}: ${progress}% - ${currentStage}...`,
        duration: 30000,
        job_id: testJobId
      };
      
      console.log('Simulating progress update:', mockMessage);
      handleJobStatusMessage(mockMessage);
    }, 1000);
  };

  // Test function to directly test the progress indicator
  const testProgressIndicator = () => {
    const isLovableEnvironment = 
      typeof window !== 'undefined' && 
      (window.location.hostname === 'localhost' || 
       window.location.hostname.includes('lovable'));
    
    if (!isLovableEnvironment) {
      console.warn('Test only works in Lovable/localhost');
      return;
    }

    console.log('🧪 Testing progress indicator directly...');
    
    // Test message with proper format
    const testMessage: JobStatusMessage = {
      screens: 'index',
      html: 'test-screen: 75% - Rendering amazing content...',
      duration: 10000,
      job_id: 'test-job-' + nanoid()
    };
    
    console.log('🧪 Calling handleJobStatusMessage with test message:', testMessage);
    handleJobStatusMessage(testMessage);
  };

  // Cleanup effect for job statuses
  useEffect(() => {
    return () => {
      Object.values(jobStatuses).forEach(status => {
        if (status.timeoutId) {
          clearTimeout(status.timeoutId);
        }
      });
    };
  }, [jobStatuses]);

  // Progress indicator - show active job messages
  const activeJobs = Object.values(jobStatuses).filter(status => status.visible);
  const progressIndicator = activeJobs.length > 0 ? (
    <div className="fixed bottom-4 left-4 z-50">
      <div className="bg-gray-900/90 text-white rounded-lg p-3 max-w-md">
        {/* Header with expand/collapse control */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">Active Jobs ({activeJobs.length})</span>
            <button
              onClick={() => setStatusPanelExpanded(!statusPanelExpanded)}
              className="text-gray-400 hover:text-white text-xs p-1"
              title={statusPanelExpanded ? "Collapse" : "Expand"}
            >
              {statusPanelExpanded ? "−" : "+"}
            </button>
          </div>
        </div>
        
        {/* Job list - only show if expanded */}
        {statusPanelExpanded && (
          <div className="space-y-2">
            {activeJobs.map((job, index) => {
              // Extract pub_dest from message format: "pub_dest: message"
              const pubDestMatch = job.message.match(/^([^:]+):/);
              const pubDest = pubDestMatch ? pubDestMatch[1].trim() : null;
              
              // Extract progress percentage from message
              const progressMatch = job.message.match(/(\d+)%/);
              const progressPercent = progressMatch ? parseInt(progressMatch[1]) : null;
              
              // Strip HTML and get clean status text
              const tempDiv = document.createElement('div');
              tempDiv.innerHTML = job.message;
              const cleanText = tempDiv.textContent || tempDiv.innerText || job.message;
              
              // Remove pub_dest prefix and percentage from status text
              let statusText = cleanText;
              if (pubDest) {
                statusText = statusText.replace(new RegExp(`^${pubDest}:\\s*`), '');
              }
              if (progressPercent !== null) {
                statusText = statusText.replace(/^\d+%\s*-\s*/, '');
              }
              
              return (
                <div key={job.id} className="text-sm border-l-2 border-gray-700 pl-2">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-yellow-300 font-mono text-xs">Job {index + 1}</span>
                      {pubDest && (
                        <span className="text-blue-300 text-xs">{pubDest}</span>
                      )}
                      {progressPercent !== null && (
                        <span className="text-green-300 text-xs font-mono">{progressPercent}%</span>
                      )}
                    </div>
                    <button
                      onClick={() => handleCloseJobStatus(job.id)}
                      className="text-gray-400 hover:text-red-400 text-xs p-1"
                      title="Remove job"
                    >
                      ×
                    </button>
                  </div>
                  <div className="text-gray-200 text-xs">{statusText}</div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  ) : null;

  useEffect(() => {
    const fetchDestinations = async () => {
      try {
        const destinations = await apiService.getPublishDestinations();
        setPublishDestinations(destinations);
      } catch (error) {
        console.error('Error fetching publish destinations:', error);
      }
    };

    fetchDestinations();
  }, []);

  // Add DnD sensors
  const sensors = useSensors(
    useSensor(MouseSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(TouchSensor, {
      activationConstraint: {
        delay: 200,
        tolerance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Handle drag end events
  const handleDragEnd = (event: any) => {
    const { active, over } = event;
    if (!active || !over) return;
    
    // For tab drops, let ImageDisplay handle it and return early
    if (typeof over.id === 'string' && over.id.startsWith(DROP_ZONES.TAB_PREFIX)) {
      console.log('Tab drop detected in Index - letting ImageDisplay handle it');
      return;
    }
    
    // Handle drops on the prompt area
    if (over.id === DROP_ZONES.PROMPT) {
      const imageId = active.id;
      console.log('Image dropped on prompt area, ID:', imageId);
      
      if (active.data?.current) {
        console.log('ACTIVE DATA FOR DND:', JSON.stringify(active.data.current, null, 2));
        
        // Use our shared utility function with active data
        const referenceUrl = getReferenceUrl(active.data.current);
        if (referenceUrl) {
          console.log('Using reference URL:', referenceUrl);
          // Use the context function
          addReferenceUrl(referenceUrl, true);
          toast.success('Image added as reference');
          return;
        }
      }
      
      // Try to find image in generated images as fallback
      const generatedImage = generatedImages.find(img => 
        img.id === imageId || 
        img.url.includes(imageId)
      );
      
      if (generatedImage) {
        console.log('Found in generated images, using URL:', generatedImage.url);
        // Use the existing function which will trigger the event system
        handleUseGeneratedAsInput(generatedImage.url, true);
        return;
      }
      
      console.error('Could not find image URL, fallback to ID:', imageId);
      toast.error('Failed to add image as reference');
      return;
    }
  };

  // Use our adapter to sync global state with context
  useReferenceImagesAdapter();

  return (
    <>
      <MainLayout
        onToggleConsole={toggleConsole}
        consoleVisible={consoleVisible}
        onOpenAdvancedOptions={handleOpenAdvancedOptions}
        consoleLogs={consoleLogs}
        onClearConsole={clearConsole}
        isFirstRun={isFirstRun}
      >
        {isFirstRun && <IntroText />}
        
        <DndContext 
          sensors={sensors} 
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
        <PromptForm 
          onSubmit={handlePromptSubmit}
          isLoading={activeGenerations.length > 0}
          currentPrompt={currentPrompt}
          isFirstRun={isFirstRun}
          onOpenAdvancedOptions={handleOpenAdvancedOptions}
          selectedWorkflow={currentWorkflow}
          selectedRefiner={selectedRefiner}
          selectedPublish={selectedPublish}
          workflowParams={currentParams}
          refinerParams={refinerParams}
          globalParams={currentGlobalParams}
          onWorkflowChange={setCurrentWorkflow}
          onRefinerChange={handleRefinerChange}
          onPublishChange={handlePublishChange}
        />
        
        <ImageDisplay 
          imageUrl={imageUrl}
          prompt={currentPrompt}
          isLoading={activeGenerations.length > 0}
          uploadedImages={uploadedImageUrls}
          generatedImages={generatedImages}
          imageContainerOrder={imageContainerOrder}
          expandedContainers={expandedContainers}
          setExpandedContainers={setExpandedContainers}
          workflow={currentWorkflow}
          generationParams={currentParams}
          onUseGeneratedAsInput={handleUseGeneratedAsInput}
          onCreateAgain={handleCreateAgain}
          onReorderContainers={handleReorderContainers}
          onDeleteImage={handleDeleteImage}
          onDeleteContainer={handleDeleteContainer}
          fullscreenRefreshTrigger={fullscreenRefreshTrigger}
          publishDestinations={publishDestinations.map(dest => dest.id)}
        />
        </DndContext>
      </MainLayout>
      
      <AdvancedOptionsContainer
        isOpen={advancedOptionsOpen}
        onOpenChange={handleAdvancedOptionsOpenChange}
        workflows={typedWorkflows}
        selectedWorkflow={currentWorkflow}
        currentParams={currentParams}
        currentGlobalParams={currentGlobalParams}
        onWorkflowChange={setCurrentWorkflow}
        onParamsChange={setCurrentParams}
        onGlobalParamChange={(paramId: string, value: any) => {
          setCurrentGlobalParams(prev => ({
            ...prev,
            [paramId]: value
          }));
        }}
        selectedRefiner={selectedRefiner}
        refinerParams={refinerParams}
        onRefinerChange={handleRefinerChange}
        onRefinerParamChange={handleRefinerParamChange}
      />
      
      {wsConnected && (
        <div className="fixed bottom-4 right-4 p-1 px-2 bg-green-100 text-green-800 text-xs rounded-full border border-green-300 opacity-60">
          <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-1"></span>
          Connected
        </div>
      )}
      
      {/* OLD OVERLAY SYSTEM REMOVED - this was the red blob */}
      
      {/* Debug panel - shows job status info */}
      {/* Debug panel to show job statuses (always visible in development) */}
      {/* Debug mode: always show progress indicator with fake data in development */}
      {/* Debug jobs are now handled by the new progress indicator */}
      
      {/* New progress indicator */}
      {progressIndicator}
      
      {(window.location.hostname === 'localhost' || window.location.hostname.includes('lovable')) && (
        <div className="fixed bottom-0 left-0 right-0 bg-gray-100 p-4 border-t flex items-center space-x-2 z-50">
          <input 
            type="text"
            placeholder="Job ID (optional)"
            value={devJobId}
            onChange={(e) => setDevJobId(e.target.value)}
            className="flex-1 p-2 border rounded"
          />
          <input 
            type="text"
            placeholder="HTML Message"
            value={devHtml}
            onChange={(e) => setDevHtml(e.target.value)}
            className="flex-1 p-2 border rounded"
          />
          <button 
            onClick={simulateWebSocketMessage}
            className="bg-blue-500 text-white p-2 rounded hover:bg-blue-600"
          >
            Simulate WS Message
          </button>
          <button 
            onClick={simulateProgressUpdate}
            className="bg-purple-500 text-white p-2 rounded hover:bg-purple-600"
          >
            Simulate Progress Update
          </button>
          <button 
            onClick={testProgressIndicator}
            className="bg-red-500 text-white p-2 rounded hover:bg-red-600"
          >
            Test Progress Indicator
          </button>
        </div>
      )}
    </>
  );
};

export default Index;
