import React, { useState, useEffect, useCallback, useRef, useLayoutEffect } from 'react';
// @ts-ignore
import Form from '@rjsf/mui';
// @ts-ignore
import validator from '@rjsf/validator-ajv8';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import { Button, Dialog as MuiDialog, DialogContent as MuiDialogContent, 
  DialogTitle as MuiDialogTitle, DialogActions, Tabs, Tab, 
  Select, MenuItem, TextField, Dialog, DialogTitle, DialogContent, List, ListItem, ListItemText, IconButton, Switch, FormControlLabel } from '@mui/material';
import { Check, X, Save, FolderOpen, ArrowUp, ArrowDown, Trash2, Plus } from 'lucide-react';
import apiService from '../../utils/api';
import { toast } from 'sonner';
import { useIsMobile } from '@/hooks/useIsMobile';

interface SchemaEditModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  destination: string;
  schema: string;
  initialData: any;
  saveEndpoint: string;
  saveMethod: string;
  onSave: () => void;
  scriptsDirectory?: string; // Optional scripts directory path
}

// Create a more balanced theme with better font sizes
const theme = createTheme({
  typography: {
    fontSize: 14,
    h6: {
      fontSize: '1.1rem',
    },
    body1: {
      fontSize: '0.875rem',
    },
    body2: {
      fontSize: '0.8rem',
    },
    button: {
      fontSize: '0.8rem',
    },
  },
  components: {
    MuiTab: {
      styleOverrides: {
        root: {
          fontSize: '0.8rem',
          minHeight: '40px',
          padding: '8px 12px',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          fontSize: '0.8rem',
          padding: '6px 12px',
          minWidth: 'auto',
        },
        sizeSmall: {
          fontSize: '0.75rem',
          padding: '4px 8px',
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          fontSize: '1rem',
          padding: '12px 16px',
        },
      },
    },
    MuiDialogContent: {
      styleOverrides: {
        root: {
          padding: '8px 16px',
        },
      },
    },
    MuiDialogActions: {
      styleOverrides: {
        root: {
          padding: '12px 16px',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiInputBase-root': {
            fontSize: '0.875rem',
          },
          '& .MuiInputLabel-root': {
            fontSize: '0.875rem',
          },
        },
      },
    },
    MuiFormControl: {
      styleOverrides: {
        root: {
          margin: '8px 0',
        },
      },
    },
    MuiFormLabel: {
      styleOverrides: {
        root: {
          fontSize: '0.8rem',
        },
      },
    },
  },
});

// Helper to compute depth from item.path (e.g., 'root.0.1.2')
function getDepth(item: any) {
  if (item && item.path && typeof item.path === 'string') {
    return Math.max(0, item.path.split('.').length - 1);
  }
  return 0;
}

// Custom array field template for mobile to overlay controls and save width
const MobileArrayFieldTemplate = (props: any) => {
  const { items, canAdd, onAddClick, title } = props;
  const maxLeftPad = 32;
  return (
    <div style={{ width: '100%' }}>
      {title && (
        <div style={{ fontWeight: 600, fontSize: '1rem', margin: '8px 0 2px 0' }}>{title}</div>
      )}
      {items &&
        items.map((item: any) => {
          const depth = getDepth(item);
          const leftPad = Math.min(4 * (depth + 1), maxLeftPad);
          const controlPosition = leftPad === maxLeftPad
            ? { left: leftPad }
            : { right: 4 };
          return (
            <div key={item.key} style={{
              position: 'relative',
              marginBottom: '4px',
              border: '1px solid #e0e0e0',
              borderRadius: '6px',
              padding: `4px 8px 4px ${leftPad}px`,
              background: item.index % 2 === 0 ? '#fafbfc' : '#f5f6f8',
              boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
              minHeight: 40,
              borderLeft: '2px solid #e0e0e0',
              overflowX: 'auto',
              boxSizing: 'border-box',
            }}>
              {/* Item content */}
              <div>{item.children}</div>
              {/* Floating inline control buttons */}
              <div style={{
                position: 'absolute',
                top: 4,
                display: 'flex',
                flexDirection: 'row',
                gap: '4px',
                background: 'rgba(245,245,245,0.85)',
                borderRadius: '6px',
                boxShadow: '0 1px 2px rgba(0,0,0,0.07)',
                padding: '2px 4px',
                zIndex: 2,
                minWidth: 0,
                ...controlPosition,
              }}>
                {item.hasMoveUp && (
                  <IconButton size="small" onClick={item.onReorderClick(item.index, item.index - 1)} style={{ width: 28, height: 28 }}>
                    <ArrowUp style={{ width: 18, height: 18 }} />
                  </IconButton>
                )}
                {item.hasMoveDown && (
                  <IconButton size="small" onClick={item.onReorderClick(item.index, item.index + 1)} style={{ width: 28, height: 28 }}>
                    <ArrowDown style={{ width: 18, height: 18 }} />
                  </IconButton>
                )}
                {item.hasRemove && (
                  <IconButton size="small" onClick={item.onDropIndexClick(item.index)} style={{ width: 28, height: 28 }}>
                    <Trash2 style={{ width: 18, height: 18 }} />
                  </IconButton>
                )}
              </div>
            </div>
          );
        })}
      {canAdd && (
        <div style={{ textAlign: 'right', marginTop: '2px' }}>
          <IconButton size="medium" onClick={onAddClick} style={{ width: 32, height: 32, background: '#e6f4ea', borderRadius: 6 }}>
            <Plus style={{ width: 20, height: 20 }} />
          </IconButton>
        </div>
      )}
    </div>
  );
};

export const SchemaEditModal: React.FC<SchemaEditModalProps> = ({
  open,
  onOpenChange,
  destination,
  schema,
  initialData,
  saveEndpoint,
  saveMethod,
  onSave,
  scriptsDirectory
}) => {
  const isMobile = useIsMobile();
  const [showGuidance, setShowGuidance] = useState(false); // default OFF
  
  // Only log on initial render with useEffect instead of every render
  useEffect(() => {
    if (process.env.NODE_ENV === 'development') {
      console.log('MODAL: Schema initialized with type:', typeof schema);
      console.log('MODAL: Initial data:', initialData);
    }
  }, [schema, initialData]);
  
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState<any>(initialData || {});
  const [activeTab, setActiveTab] = useState(0);
  const [isValid, setIsValid] = useState<boolean>(true);
  const [rawText, setRawText] = useState<string>(JSON.stringify(initialData || {}, null, 2));
  const [isUpdatingFromTab, setIsUpdatingFromTab] = useState(false);
  const [isUpdatingFromEditor, setIsUpdatingFromEditor] = useState(false);
  const initializedRef = useRef(false);
  const formKey = useRef(0);
  
  // For file operations
  const [scriptFiles, setScriptFiles] = useState<string[]>([]);
  const [loadDialogOpen, setLoadDialogOpen] = useState(false);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveFileName, setSaveFileName] = useState("");
  
  // Add CSS styles for better form appearance
  const formStyles = `
    .hide-guidance .MuiTypography-root.MuiTypography-subtitle2,
    .hide-guidance .description,
    .hide-guidance .field-description {
      display: none !important;
    }
    /* Guidance/help/description text for mobile */
    .mobile-form .MuiTypography-root.MuiTypography-subtitle2 {
      font-size: 0.75rem !important;
      color: #aaa !important;
      font-weight: 400 !important;
      margin: 2px 0 4px 0 !important;
      line-height: 1.3 !important;
    }
    .mobile-form .MuiTypography-root.MuiTypography-body1,
    .mobile-form .MuiTypography-root.MuiTypography-body2,
    .mobile-form .field-description,
    .mobile-form .description,
    .mobile-form h5 + p,
    .mobile-form h6 + p,
    .mobile-form legend + p,
    .mobile-form h5 + div,
    .mobile-form h6 + div,
    .mobile-form legend + div {
      font-size: 0.75rem !important;
      color: #aaa !important;
      font-weight: 400 !important;
      margin: 2px 0 4px 0 !important;
      line-height: 1.3 !important;
    }
    .mobile-form .MuiFormControlLabel-root {
      padding-left: 4px !important;
      margin-left: 0 !important;
    }
    /* Make textboxes and dropdowns more touch-friendly */
    .mobile-form .MuiInputBase-root,
    .mobile-form .MuiSelect-select {
      min-height: 36px !important;
      padding-top: 8px !important;
      padding-bottom: 8px !important;
      font-size: 1rem !important;
    }
    .mobile-form {
      overflow-x: auto !important;
      box-sizing: border-box !important;
    }
    .mobile-form * {
      box-sizing: border-box !important;
    }
    .mobile-form .MuiFormHelperText-root {
      margin-bottom: 2px !important;
    }
    .mobile-form .MuiPaper-root,
    .mobile-form .MuiCardContent-root,
    .mobile-form .MuiCardHeader-root,
    .mobile-form fieldset {
      padding-top: 4px !important;
      padding-bottom: 4px !important;
      margin-top: 4px !important;
      margin-bottom: 4px !important;
    }
    .mobile-form .array-item {
      margin-bottom: 4px !important;
      padding-top: 4px !important;
      padding-bottom: 4px !important;
      overflow-x: auto !important;
      box-sizing: border-box !important;
    }
    .mobile-form .MuiGrid-item {
      padding-top: 2px !important;
      padding-bottom: 2px !important;
    }
    .mobile-form .MuiFormControl-root {
      margin: 2px 0 !important;
      width: 100% !important;
    }
    .mobile-form .MuiInputBase-root {
      font-size: 0.75rem !important;
      padding: 2px 4px !important;
    }
    .mobile-form .MuiInputLabel-root {
      font-size: 0.75rem !important;
    }
    .mobile-form .MuiFormLabel-root {
      font-size: 0.7rem !important;
      margin-bottom: 1px !important;
    }
    .mobile-form .MuiPaper-root {
      padding: 2px !important;
      margin: 2px 0 !important;
    }
    .mobile-form .MuiCardContent-root {
      padding: 4px !important;
    }
    .mobile-form .MuiCardHeader-root {
      padding: 4px !important;
    }
    .mobile-form .MuiTypography-h5 {
      font-size: 0.8rem !important;
    }
    .mobile-form .MuiTypography-h6 {
      font-size: 0.75rem !important;
    }
    .mobile-form .MuiBox-root {
      padding: 0 !important;
      margin: 0 !important;
    }
    .mobile-form fieldset {
      margin: 0 !important;
      padding: 4px !important;
      border: 1px solid #ddd !important;
    }
    .mobile-form legend {
      font-size: 0.7rem !important;
      padding: 2px 4px !important;
      margin: 0 !important;
    }
    .mobile-form .MuiGrid-container {
      margin: 0 !important;
      width: 100% !important;
    }
    .mobile-form .MuiGrid-item {
      padding: 1px !important;
    }
    /* Override all container and wrapper padding */
    .mobile-form .MuiContainer-root,
    .mobile-form .form-group,
    .mobile-form .field,
    .mobile-form .field-object,
    .mobile-form .field-array {
      padding: 0 !important;
      margin: 2px 0 !important;
      width: 100% !important;
    }
    /* Make array items extremely compact */
    .mobile-form .array-item {
      display: flex !important;
      align-items: flex-start !important;
      gap: 2px !important;
      margin: 1px 0 !important;
      padding: 0 !important;
      width: 100% !important;
    }
    .mobile-form .array-item > div:first-child {
      flex: 1 !important;
      min-width: 0 !important;
      overflow: hidden !important;
    }
    /* Ultra-compact button controls */
    .mobile-form .array-item-toolbox,
    .mobile-form .btn-group {
      display: flex !important;
      flex-direction: column !important;
      width: 20px !important;
      gap: 1px !important;
      margin: 0 !important;
      padding: 0 !important;
      flex-shrink: 0 !important;
    }
    .mobile-form .array-item-toolbox button,
    .mobile-form .array-item-toolbox .MuiIconButton-root,
    .mobile-form .btn-group button,
    .mobile-form .array-item-move-up,
    .mobile-form .array-item-move-down,
    .mobile-form .array-item-remove {
      width: 20px !important;
      height: 20px !important;
      padding: 0 !important;
      margin: 0 !important;
      min-width: 20px !important;
      min-height: 20px !important;
      border-radius: 2px !important;
      font-size: 0.6rem !important;
    }
    .mobile-form .array-item-toolbox svg,
    .mobile-form .btn-group svg {
      width: 12px !important;
      height: 12px !important;
    }
    /* Fix row layout */
    .mobile-form .row {
      display: flex !important;
      margin: 0 !important;
      width: 100% !important;
      align-items: flex-start !important;
    }
    .mobile-form .col-xs-9,
    .mobile-form .col-sm-9,
    .mobile-form .col-md-9 {
      flex: 1 !important;
      padding: 0 !important;
      margin: 0 !important;
      min-width: 0 !important;
      overflow: hidden !important;
    }
    .mobile-form .col-xs-3,
    .mobile-form .col-sm-3,
    .mobile-form .col-md-3 {
      width: 20px !important;
      padding: 0 !important;
      margin: 0 !important;
      flex-shrink: 0 !important;
    }
    /* Override all bootstrap column classes */
    .mobile-form [class*="col-"] {
      padding: 0 !important;
    }
    /* Compact form buttons */
    .mobile-form .MuiButton-root {
      font-size: 0.65rem !important;
      padding: 2px 4px !important;
      margin: 1px !important;
      min-width: auto !important;
    }
    .mobile-form .array-item-add button {
      font-size: 0.65rem !important;
      padding: 2px 6px !important;
      margin: 2px 0 !important;
      height: 24px !important;
    }
    /* Compact checkboxes and radios */
    .mobile-form .MuiCheckbox-root,
    .mobile-form .MuiRadio-root {
      padding: 2px !important;
    }
    /* Compact selects */
    .mobile-form .MuiSelect-select {
      padding: 2px 4px !important;
      font-size: 0.75rem !important;
    }
    /* Compact help text */
    .mobile-form .MuiFormHelperText-root {
      margin: 1px 0 0 0 !important;
      font-size: 0.65rem !important;
    }
    /* Nested arrays with minimal indentation */
    .mobile-form .field-array .field-array {
      padding-left: 0 !important;
      border-left: none !important;
    }
    /* Ensure no horizontal overflow */
    .mobile-form,
    .mobile-form > *,
    .mobile-form .field,
    .mobile-form .array-item {
      max-width: 100% !important;
      overflow-x: hidden !important;
    }
    /* Desktop form - keep reasonable spacing */
    .desktop-form .MuiFormControl-root {
      margin: 8px 0 !important;
    }
    .desktop-form .MuiInputBase-root {
      font-size: 0.875rem !important;
    }
    .desktop-form .MuiInputLabel-root {
      font-size: 0.875rem !important;
    }
    .desktop-form .MuiFormLabel-root {
      font-size: 0.8rem !important;
    }
    .desktop-form .MuiPaper-root {
      padding: 12px !important;
      margin: 8px 0 !important;
    }
    .desktop-form .MuiCardContent-root {
      padding: 16px !important;
    }
    .desktop-form fieldset {
      margin: 8px 0 !important;
      padding: 12px !important;
    }
    .desktop-form .MuiContainer-root {
      padding: 0 !important;
    }
    .mobile-form h5, .mobile-form legend, .mobile-form .MuiTypography-h5 {
      font-size: 1.1rem !important;
      font-weight: 700 !important;
      margin: 16px 0 6px 0 !important;
      color: #222 !important;
    }
    .mobile-form h6, .mobile-form .MuiTypography-h6 {
      font-size: 1rem !important;
      font-weight: 600 !important;
      margin: 12px 0 4px 0 !important;
      color: #333 !important;
    }
    .mobile-form label, .mobile-form .MuiFormLabel-root {
      font-size: 0.95rem !important;
      font-weight: 500 !important;
      color: #222 !important;
      margin-bottom: 2px !important;
    }
    .mobile-form .MuiFormHelperText-root {
      font-size: 0.8rem !important;
      color: #666 !important;
      margin-bottom: 4px !important;
    }
    .mobile-form .field-description,
    .mobile-form .description,
    .mobile-form .MuiFormHelperText-root {
      font-size: 0.75rem !important;
      color: #aaa !important;
      font-weight: 400 !important;
      margin: 2px 0 4px 0 !important;
      line-height: 1.3 !important;
    }
    .mobile-form .MuiFormHelperText-root {
      margin-bottom: 2px !important;
    }
    .mobile-form .MuiPaper-root,
    .mobile-form .MuiCardContent-root,
    .mobile-form .MuiCardHeader-root,
    .mobile-form fieldset {
      padding-top: 4px !important;
      padding-bottom: 4px !important;
      margin-top: 4px !important;
      margin-bottom: 4px !important;
    }
    .mobile-form .array-item {
      margin-bottom: 4px !important;
      padding-top: 4px !important;
      padding-bottom: 4px !important;
    }
    .mobile-form .MuiGrid-item {
      padding-top: 2px !important;
      padding-bottom: 2px !important;
    }
    /* Guidance/help/description text for mobile */
    .mobile-form .MuiTypography-root.MuiTypography-body1,
    .mobile-form .MuiTypography-root.MuiTypography-body2,
    .mobile-form .field-description,
    .mobile-form .description,
    .mobile-form h5 + p,
    .mobile-form h6 + p,
    .mobile-form legend + p,
    .mobile-form h5 + div,
    .mobile-form h6 + div,
    .mobile-form legend + div {
      font-size: 0.75rem !important;
      color: #aaa !important;
      font-weight: 400 !important;
      margin: 2px 0 4px 0 !important;
      line-height: 1.3 !important;
    }
    .mobile-form .MuiFormHelperText-root {
      margin-bottom: 2px !important;
    }
    .mobile-form .MuiPaper-root,
    .mobile-form .MuiCardContent-root,
    .mobile-form .MuiCardHeader-root,
    .mobile-form fieldset {
      padding-top: 4px !important;
      padding-bottom: 4px !important;
      margin-top: 4px !important;
      margin-bottom: 4px !important;
    }
    .mobile-form .array-item {
      margin-bottom: 4px !important;
      padding-top: 4px !important;
      padding-bottom: 4px !important;
    }
    .mobile-form .MuiGrid-item {
      padding-top: 2px !important;
      padding-bottom: 2px !important;
    }
    /* Guidance/help/description text for mobile */
    .mobile-form .MuiTypography-root.MuiTypography-subtitle2 {
      font-size: 0.75rem !important;
      color: #aaa !important;
      font-weight: 400 !important;
      margin: 2px 0 4px 0 !important;
      line-height: 1.3 !important;
    }
    .mobile-form .MuiFormControlLabel-root {
      padding-left: 4px !important;
      margin-left: 0 !important;
    }
  `;

  // Use useLayoutEffect to ensure form data is set synchronously before first render
  useLayoutEffect(() => {
    if (initialData) {
      setFormData(initialData);
      setRawText(JSON.stringify(initialData, null, 2));
      // Increment form key to force re-render
      formKey.current += 1;
    }
  }, [initialData]);

  // Force sync between tabs when component mounts and whenever modal opens
  useEffect(() => {
    if (open) {
      // Ensure both form data and raw text are synchronized
      if (initialData) {
        setFormData(initialData);
        setRawText(JSON.stringify(initialData, null, 2));
        initializedRef.current = true;
        // Increment form key to force re-render
        formKey.current += 1;
      }
      
      // Start with Editor tab active
      setActiveTab(0);
    }
  }, [open, initialData]);

  // Handle tab switching
  useEffect(() => {
    // Prevent circular updates
    if (isUpdatingFromTab) {
      setIsUpdatingFromTab(false);
      return;
    }

    setIsUpdatingFromTab(true);
    if (activeTab === 0) {
      // When switching to form tab, increment form key to force re-render
      formKey.current += 1;
    } else if (activeTab === 1) {
      // When switching to raw tab, update rawText from formData
      setRawText(JSON.stringify(formData, null, 2));
    }
  }, [activeTab]);

  // This effect handles parsing rawText when editing in raw mode
  useEffect(() => {
    // Only process rawText changes when in raw tab and not during tab switch
    if (activeTab !== 1 || isUpdatingFromTab) return;

    const timeoutId = setTimeout(() => {
      try {
        const newData = JSON.parse(rawText);
        // If we're already setting form data from the editor, don't update again
        if (!isUpdatingFromEditor) {
          setFormData(newData);
          // Increment form key to force re-render when switching back
          formKey.current += 1;
        }
        setIsValid(validateJson(newData));
      } catch {
        setIsValid(false);
      }
    }, 500);

    return () => clearTimeout(timeoutId);
  }, [rawText, activeTab, isUpdatingFromTab]);

  const validateJson = useCallback((json: any) => {
    try {
      const schemaObj = JSON.parse(schema);
      const result = validator.validateFormData(json, schemaObj);
      return result.errors.length === 0;
    } catch {
      return false;
    }
  }, [schema]);

  // Load script files from directory
  const fetchScriptFiles = async () => {
    if (!scriptsDirectory) return;
    
    try {
      const files = await apiService.listFiles(scriptsDirectory);
      
      // Filter to only show .json files
      const jsonFiles = files.filter((file: string) => 
        file.endsWith('.json')
      );
      setScriptFiles(jsonFiles);
    } catch (error) {
      console.error('Error fetching script files:', error instanceof Error ? error.message : String(error));
      setError('Failed to load script files');
      toast.error('Failed to load script files');
    }
  };

  // Load a script file
  const handleLoadScript = async (filename: string) => {
    if (!scriptsDirectory) return;
    
    try {
      const filePath = `${scriptsDirectory}/${filename}`;
      const data = await apiService.readFile(filePath);
      
      if (data) {
        setFormData(data);
        setRawText(JSON.stringify(data, null, 2));
        formKey.current += 1; // Force form re-render
        setLoadDialogOpen(false);
        toast.success(`Loaded ${filename}`);
      }
    } catch (error) {
      console.error('Error loading script file:', error instanceof Error ? error.message : String(error));
      setError('Failed to load script file');
      toast.error('Failed to load script file');
    }
  };

  // Save script to file
  const handleSaveScript = async () => {
    if (!scriptsDirectory || !saveFileName) return;
    
    // Ensure filename has .json extension
    const filename = saveFileName.endsWith('.json') 
      ? saveFileName 
      : `${saveFileName}.json`;
    
    try {
      const filePath = `${scriptsDirectory}/${filename}`;
      const content = JSON.stringify(formData, null, 2);
      
      await apiService.writeFile(filePath, content);
      
      setSaveDialogOpen(false);
      setSaveFileName("");
      // Refresh the file list
      fetchScriptFiles();
      toast.success(`Saved ${filename}`);
    } catch (error) {
      console.error('Error saving script file:', error instanceof Error ? error.message : String(error));
      setError('Failed to save script file');
      toast.error('Failed to save script file');
    }
  };

  const handleFormChange = (e: any) => {
    // Make sure formData exists before using it
    if (!e.formData) return;
    
    setIsUpdatingFromEditor(true);
    setFormData(e.formData);
    
    // Update rawText regardless of which tab is active
    // This ensures form edits are never lost when switching tabs
    setRawText(JSON.stringify(e.formData, null, 2));
    
    setIsUpdatingFromEditor(false);
  };

  const handleRawTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setRawText(e.target.value);
  };

  const handleSubmit = async (data: any) => {
    if (!validateJson(data)) {
      setError('Invalid form data');
      return;
    }

    try {
      // Only log in development mode
      if (process.env.NODE_ENV === 'development') {
        console.log("Submitting data:", data);
      }

      // Ensure we're sending the schedule data in the correct format
      const scheduleData = activeTab === 1 ? JSON.parse(rawText) : data;
      
      // Send the schedule data directly without wrapping it
      const response = await fetch(saveEndpoint, {
        method: saveMethod,
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(scheduleData),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || 'Failed to save');
      }

      // Wait a moment to ensure the schedule is saved
      await new Promise(resolve => setTimeout(resolve, 500));
      
      onSave();
      onOpenChange(false);
    } catch (e) {
      console.error('Error saving schedule:', e);
      setError(e instanceof Error ? e.message : 'Failed to save');
    }
  };

  const handleCancel = () => {
    onOpenChange(false);
  };

  // Parse schema
  let schemaObj = {};
  try {
    schemaObj = JSON.parse(schema);
  } catch (e) {
    if (process.env.NODE_ENV === 'development') {
      console.error('Error parsing schema:', e);
    }
  }

  return (
    <ThemeProvider theme={theme}>
      <style>{formStyles}</style>
      <MuiDialog 
        open={open} 
        onClose={() => onOpenChange(false)}
        maxWidth={isMobile ? false : "lg"}
        fullWidth
        fullScreen={isMobile}
        style={{ zIndex: 1300 }}
        PaperProps={{
          style: {
            margin: isMobile ? 0 : 16,
            maxHeight: isMobile ? '100vh' : '90vh',
            height: isMobile ? '100vh' : 'auto',
          },
          className: !showGuidance ? 'hide-guidance' : ''
        }}
      >
        <MuiDialogTitle style={{ 
          padding: isMobile ? '8px 12px' : '12px 16px',
          fontSize: isMobile ? '0.9rem' : '1rem',
          borderBottom: '1px solid #e0e0e0'
        }}>
          <div style={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center',
            flexWrap: isMobile ? 'wrap' : 'nowrap',
            gap: isMobile ? '6px' : '8px'
          }}>
            <span style={{ 
              fontSize: isMobile ? '0.85rem' : '1rem',
              fontWeight: 500,
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: isMobile ? 'nowrap' : 'normal'
            }}>
              {isMobile ? `Edit ${destination}` : `Edit Schedule for ${destination}`}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <FormControlLabel
                control={<Switch checked={showGuidance} onChange={e => setShowGuidance(e.target.checked)} size="small" />}
                label="Guidance"
                labelPlacement="start"
                style={{ marginRight: 0, marginLeft: 0 }}
              />
            </div>
            {scriptsDirectory && (
              <div style={{ 
                display: 'flex', 
                gap: isMobile ? '6px' : '8px',
                flexShrink: 0
              }}>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<FolderOpen size={isMobile ? 14 : 16} />}
                  onClick={() => {
                    fetchScriptFiles();
                    setLoadDialogOpen(true);
                  }}
                  style={{
                    fontSize: isMobile ? '0.7rem' : '0.8rem',
                    padding: isMobile ? '4px 6px' : '6px 8px',
                    minWidth: 'auto'
                  }}
                >
                  {isMobile ? '' : 'Load'}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<Save size={isMobile ? 14 : 16} />}
                  onClick={() => setSaveDialogOpen(true)}
                  style={{
                    fontSize: isMobile ? '0.7rem' : '0.8rem',
                    padding: isMobile ? '4px 6px' : '6px 8px',
                    minWidth: 'auto'
                  }}
                >
                  {isMobile ? '' : 'Save'}
                </Button>
              </div>
            )}
          </div>
        </MuiDialogTitle>
        
        <Tabs 
          value={activeTab} 
          onChange={(_, newValue) => setActiveTab(newValue)}
          style={{ 
            borderBottom: '1px solid #e0e0e0',
            minHeight: isMobile ? '36px' : '40px'
          }}
          variant={isMobile ? "fullWidth" : "standard"}
        >
          <Tab 
            label="Editor" 
            style={{ 
              fontSize: isMobile ? '0.75rem' : '0.8rem',
              minHeight: isMobile ? '36px' : '40px',
              padding: isMobile ? '6px 8px' : '8px 12px'
            }} 
          />
          <Tab 
            label={
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                Raw {isValid ? 
                  <Check style={{ width: isMobile ? '14px' : '16px', height: isMobile ? '14px' : '16px', color: 'green' }} /> : 
                  <X style={{ width: isMobile ? '14px' : '16px', height: isMobile ? '14px' : '16px', color: 'red' }} />
                }
              </div>
            }
            style={{ 
              fontSize: isMobile ? '0.75rem' : '0.8rem',
              minHeight: isMobile ? '36px' : '40px',
              padding: isMobile ? '6px 8px' : '8px 12px'
            }}
          />
          <Tab 
            label="Schema" 
            style={{ 
              fontSize: isMobile ? '0.75rem' : '0.8rem',
              minHeight: isMobile ? '36px' : '40px',
              padding: isMobile ? '6px 8px' : '8px 12px'
            }} 
          />
        </Tabs>

        <MuiDialogContent 
          dividers 
          style={{ 
            height: isMobile ? 'calc(100vh - 140px)' : '65vh',
            padding: isMobile ? '2px' : '12px 16px',
            overflow: 'auto'
          }}
        >
          {error && (
            <div style={{ 
              color: 'red', 
              padding: isMobile ? '4px 6px' : '8px 12px',
              fontSize: isMobile ? '0.75rem' : '0.875rem',
              backgroundColor: '#ffebee',
              borderRadius: '4px',
              marginBottom: isMobile ? '4px' : '12px'
            }}>
              {error}
            </div>
          )}
          
          {activeTab === 0 ? (
            <div className={isMobile ? 'mobile-form' : 'desktop-form'}>
              <Form
                key={`form-${formKey.current}`}
                schema={schemaObj}
                formData={formData}
                validator={validator}
                onChange={handleFormChange}
                onSubmit={({ formData }) => handleSubmit(formData)}
                liveValidate={false}
                showErrorList={false}
                noHtml5Validate={true}
                templates={isMobile ? { ArrayFieldTemplate: MobileArrayFieldTemplate } : undefined}
              >
                {/* Remove default Submit button by using empty children */}
                <></>
              </Form>
            </div>
          ) : activeTab === 1 ? (
            <div style={{ padding: 0, height: '100%' }}>
              <textarea
                style={{ 
                  width: '100%', 
                  height: '100%', 
                  fontFamily: 'monospace', 
                  fontSize: isMobile ? '0.75rem' : '0.8rem',
                  lineHeight: isMobile ? '1.3' : '1.4',
                  padding: isMobile ? '8px' : '12px',
                  border: '1px solid #ccc', 
                  borderRadius: '4px',
                  resize: 'none',
                  outline: 'none'
                }}
                value={rawText}
                onChange={handleRawTextChange}
              />
            </div>
          ) : (
            <div style={{ padding: 0, height: '100%' }}>
              <pre style={{ 
                width: '100%', 
                height: '100%', 
                fontFamily: 'monospace', 
                fontSize: isMobile ? '0.7rem' : '0.75rem',
                lineHeight: isMobile ? '1.3' : '1.4',
                padding: isMobile ? '8px' : '12px',
                border: '1px solid #ccc', 
                borderRadius: '4px', 
                overflow: 'auto',
                margin: 0,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word'
              }}>
                {JSON.stringify(schemaObj, null, 2)}
              </pre>
            </div>
          )}
        </MuiDialogContent>

        <DialogActions style={{ 
          padding: isMobile ? '8px 12px' : '12px 16px',
          borderTop: '1px solid #e0e0e0',
          gap: isMobile ? '6px' : '8px'
        }}>
          <Button 
            onClick={handleCancel} 
            variant="outlined" 
            color="secondary"
            size={isMobile ? "small" : "medium"}
            style={{
              fontSize: isMobile ? '0.75rem' : '0.8rem',
              padding: isMobile ? '6px 12px' : '8px 16px'
            }}
          >
            Cancel
          </Button>
          <Button 
            onClick={() => handleSubmit(formData)} 
            variant="contained" 
            color="primary"
            disabled={!isValid}
            size={isMobile ? "small" : "medium"}
            style={{
              fontSize: isMobile ? '0.75rem' : '0.8rem',
              padding: isMobile ? '6px 12px' : '8px 16px'
            }}
          >
            Save
          </Button>
        </DialogActions>
      </MuiDialog>

      {/* Load file dialog */}
      {scriptsDirectory && (
        <Dialog
          open={loadDialogOpen}
          onClose={() => setLoadDialogOpen(false)}
          maxWidth="sm"
          fullWidth
          fullScreen={isMobile}
          PaperProps={{
            style: {
              margin: isMobile ? 0 : 16,
            }
          }}
        >
          <DialogTitle style={{ 
            fontSize: isMobile ? '0.9rem' : '1rem',
            padding: isMobile ? '10px 12px' : '12px 16px'
          }}>
            Load Script
          </DialogTitle>
          <DialogContent style={{ 
            padding: isMobile ? '6px 12px' : '8px 16px'
          }}>
            {scriptFiles.length === 0 ? (
              <p style={{ 
                fontSize: isMobile ? '0.8rem' : '0.875rem',
                margin: 0,
                padding: isMobile ? '8px' : '12px'
              }}>
                No script files found in {scriptsDirectory}
              </p>
            ) : (
              <List style={{ padding: 0 }}>
                {scriptFiles.map((file) => (
                  <ListItem 
                    key={file}
                    onClick={() => handleLoadScript(file)}
                    style={{ 
                      padding: isMobile ? '6px 8px' : '8px 12px',
                      cursor: 'pointer',
                      borderRadius: '4px',
                      margin: isMobile ? '2px 0' : '4px 0'
                    }}
                    sx={{
                      '&:hover': {
                        backgroundColor: 'rgba(0, 0, 0, 0.04)'
                      }
                    }}
                  >
                    <ListItemText 
                      primary={file}
                      primaryTypographyProps={{
                        fontSize: isMobile ? '0.8rem' : '0.875rem'
                      }}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </DialogContent>
          <DialogActions style={{ 
            padding: isMobile ? '8px 12px' : '12px 16px'
          }}>
            <Button 
              onClick={() => setLoadDialogOpen(false)} 
              color="primary"
              size={isMobile ? "small" : "medium"}
              style={{
                fontSize: isMobile ? '0.75rem' : '0.8rem'
              }}
            >
              Cancel
            </Button>
          </DialogActions>
        </Dialog>
      )}

      {/* Save file dialog */}
      {scriptsDirectory && (
        <Dialog
          open={saveDialogOpen}
          onClose={() => setSaveDialogOpen(false)}
          maxWidth="sm"
          fullWidth
          fullScreen={isMobile}
          PaperProps={{
            style: {
              margin: isMobile ? 0 : 16,
            }
          }}
        >
          <DialogTitle style={{ 
            fontSize: isMobile ? '0.9rem' : '1rem',
            padding: isMobile ? '10px 12px' : '12px 16px'
          }}>
            Save Script
          </DialogTitle>
          <DialogContent style={{ 
            padding: isMobile ? '6px 12px' : '8px 16px'
          }}>
            <TextField
              autoFocus
              margin="dense"
              id="filename"
              label="Filename"
              type="text"
              fullWidth
              value={saveFileName}
              onChange={(e) => setSaveFileName(e.target.value)}
              helperText="The .json extension will be added automatically if omitted"
              size={isMobile ? "small" : "medium"}
              InputProps={{
                style: {
                  fontSize: isMobile ? '0.8rem' : '0.875rem'
                }
              }}
              InputLabelProps={{
                style: {
                  fontSize: isMobile ? '0.8rem' : '0.875rem'
                }
              }}
              FormHelperTextProps={{
                style: {
                  fontSize: isMobile ? '0.7rem' : '0.75rem'
                }
              }}
            />
          </DialogContent>
          <DialogActions style={{ 
            padding: isMobile ? '8px 12px' : '12px 16px'
          }}>
            <Button 
              onClick={() => setSaveDialogOpen(false)} 
              color="secondary"
              size={isMobile ? "small" : "medium"}
              style={{
                fontSize: isMobile ? '0.75rem' : '0.8rem'
              }}
            >
              Cancel
            </Button>
            <Button 
              onClick={handleSaveScript} 
              color="primary"
              disabled={!saveFileName.trim()}
              size={isMobile ? "small" : "medium"}
              style={{
                fontSize: isMobile ? '0.75rem' : '0.8rem'
              }}
            >
              Save
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </ThemeProvider>
  );
}; 