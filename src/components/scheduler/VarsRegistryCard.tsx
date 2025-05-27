import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { RefreshCcw, Edit, Trash2, Plus, Download, Upload, Globe, Users, FileText, ChevronDown, ChevronRight } from 'lucide-react';
import apiService from '@/utils/api';
import { toast } from 'sonner';
import { useIsMobile } from '@/hooks/useIsMobile';
import { useToast } from '@/components/ui/use-toast';
import { HierarchicalHeader } from './HierarchicalHeader';
import { HierarchicalContent } from './HierarchicalContent';

interface VarRegistryData {
  global: Record<string, VarInfo>;
  groups: Record<string, Record<string, VarInfo>>;
  imports: Record<string, Record<string, ImportInfo>>;
  last_updated: string;
}

interface VarInfo {
  friendly_name: string;
  owner: string;
  value: any;
  timestamp: string;
}

interface ImportInfo {
  imported_as: string;
  source: string;
  timestamp: string;
}

export const VarsRegistryCard: React.FC = () => {
  const [data, setData] = useState<VarRegistryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingVar, setEditingVar] = useState<{ scope: string; key: string; value: any } | null>(null);
  const [editValue, setEditValue] = useState('');
  const [activeTab, setActiveTab] = useState('global');
  const isMobile = useIsMobile();
  const { toast } = useToast();
  
  // Add state for collapsible sections
  const [globalSectionOpen, setGlobalSectionOpen] = useState(false);
  const [groupsSectionOpen, setGroupsSectionOpen] = useState(false);
  const [importsSectionOpen, setImportsSectionOpen] = useState(false);
  
  // Fetch the registry data
  const fetchRegistry = async () => {
    setLoading(true);
    try {
      const response = await apiService.getVarsRegistry();
      if (response && response.status === 'success') {
        setData(response.registry);
      }
    } catch (error) {
      console.error('Error fetching registry:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch variables registry',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };
  
  // Load registry data on component mount
  useEffect(() => {
    fetchRegistry();
    
    // Set up polling for registry updates (every 30 seconds)
    const intervalId = setInterval(fetchRegistry, 30000);
    
    // Clean up interval on unmount
    return () => clearInterval(intervalId);
  }, []);
  
  // Handle editing a variable
  const handleEditVar = (name: string, value: any) => {
    setEditingVar({ scope: 'global', key: name, value });
    setEditDialogOpen(true);
  };
  
  // Handle deleting a variable
  const handleDeleteVar = async (name: string) => {
    if (confirm(`Are you sure you want to delete the variable "${name}"?`)) {
      try {
        await apiService.deleteExportedVar(name);
        toast({
          title: 'Success',
          description: `Variable "${name}" deleted successfully`,
        });
        fetchRegistry(); // Refresh the registry
      } catch (error) {
        console.error('Error deleting variable:', error);
        toast({
          title: 'Error',
          description: `Failed to delete variable "${name}"`,
          variant: 'destructive',
        });
      }
    }
  };
  
  // Handle saving edits to a variable
  const handleSaveEdit = async () => {
    if (!editingVar) return;
    
    try {
      await apiService.setExportedVar(editingVar.key, editingVar.value);
      setEditDialogOpen(false);
      toast({
        title: 'Success',
        description: `Variable "${editingVar.key}" updated successfully`,
      });
      fetchRegistry(); // Refresh the registry
    } catch (error) {
      console.error('Error updating variable:', error);
      toast({
        title: 'Error',
        description: `Failed to update variable "${editingVar.key}"`,
        variant: 'destructive',
      });
    }
  };
  
  // Mobile-friendly variable card component
  const VariableCard: React.FC<{ 
    name: string; 
    info: VarInfo;
    onEdit: () => void;
    onDelete: () => void;
  }> = ({ name, info, onEdit, onDelete }) => {
    return (
      <div className="border rounded-lg p-4 space-y-2 bg-card">
        <div className="flex justify-between items-start">
          <div className="flex-1">
            <h4 className="font-semibold text-sm">{name}</h4>
            {info.friendly_name && (
              <p className="text-xs text-muted-foreground">{info.friendly_name}</p>
            )}
          </div>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={onEdit}
            >
              <Edit className="h-3 w-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={onDelete}
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        </div>
        
        <div className="space-y-1 text-xs">
          <div className="flex flex-wrap gap-1">
            <span className="text-muted-foreground">Owner:</span>
            <span className="font-medium">{info.owner}</span>
          </div>
          <div className="space-y-1">
            <span className="text-muted-foreground">Value:</span>
            <div className="bg-muted/50 p-2 rounded text-xs font-mono break-all">
              {typeof info.value === 'object'
                ? JSON.stringify(info.value, null, 2)
                : String(info.value)}
            </div>
          </div>
        </div>
      </div>
    );
  };
  
  // Render global variables
  const renderGlobalVars = () => {
    if (!data || !data.global || Object.keys(data.global).length === 0) {
      return <p className="text-muted-foreground">No global variables found</p>;
    }
    
    // Mobile layout - cards
    if (isMobile) {
      return (
        <div className="space-y-3">
          {Object.entries(data.global).map(([name, info]) => (
            <VariableCard
              key={name}
              name={name}
              info={info}
              onEdit={() => handleEditVar(name, info.value)}
              onDelete={() => handleDeleteVar(name)}
            />
          ))}
        </div>
      );
    }
    
    // Desktop layout - table
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Friendly Name</TableHead>
            <TableHead>Value</TableHead>
            <TableHead className="w-[100px]">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(data.global).map(([name, info]) => (
            <TableRow key={name}>
              <TableCell className="font-medium">{name}</TableCell>
              <TableCell>{info.owner}</TableCell>
              <TableCell>{info.friendly_name}</TableCell>
              <TableCell className="max-w-[200px] truncate">
                {typeof info.value === 'object'
                  ? JSON.stringify(info.value)
                  : String(info.value)}
              </TableCell>
              <TableCell>
                <div className="flex space-x-2">
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => handleEditVar(name, info.value)}
                  >
                    <Edit className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => handleDeleteVar(name)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  };
  
  // Render group variables
  const renderGroupVars = () => {
    if (!data || !data.groups || Object.keys(data.groups).length === 0) {
      return <p className="text-muted-foreground">No group variables found</p>;
    }
    
    return (
      <div className="space-y-6">
        {Object.entries(data.groups).map(([groupName, groupVars]) => (
          <div key={groupName} className="space-y-2">
            <h3 className="font-medium text-lg flex items-center">
              Group: <Badge className="ml-2">{groupName}</Badge>
            </h3>
            {Object.keys(groupVars).length === 0 ? (
              <p className="text-muted-foreground">No variables in this group</p>
            ) : (
              isMobile ? (
                // Mobile layout - cards
                <div className="space-y-3">
                  {Object.entries(groupVars).map(([name, info]) => (
                    <VariableCard
                      key={name}
                      name={name}
                      info={info}
                      onEdit={() => handleEditVar(name, info.value)}
                      onDelete={() => handleDeleteVar(name)}
                    />
                  ))}
                </div>
              ) : (
                // Desktop layout - table
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Owner</TableHead>
                      <TableHead>Friendly Name</TableHead>
                      <TableHead>Value</TableHead>
                      <TableHead className="w-[100px]">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(groupVars).map(([name, info]) => (
                      <TableRow key={name}>
                        <TableCell className="font-medium">{name}</TableCell>
                        <TableCell>{info.owner}</TableCell>
                        <TableCell>{info.friendly_name}</TableCell>
                        <TableCell className="max-w-[200px] truncate">
                          {typeof info.value === 'object'
                            ? JSON.stringify(info.value)
                            : String(info.value)}
                        </TableCell>
                        <TableCell>
                          <div className="flex space-x-2">
                            <Button
                              variant="outline"
                              size="icon"
                              onClick={() => handleEditVar(name, info.value)}
                            >
                              <Edit className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="outline"
                              size="icon"
                              onClick={() => handleDeleteVar(name)}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )
            )}
          </div>
        ))}
      </div>
    );
  };
  
  // Mobile-friendly import card component
  const ImportCard: React.FC<{
    importerId: string;
    info: ImportInfo;
  }> = ({ importerId, info }) => {
    return (
      <div className="border rounded-lg p-3 space-y-1 bg-card text-xs">
        <div className="font-semibold">{importerId}</div>
        <div className="space-y-1 text-muted-foreground">
          <div>Imported as: <span className="font-medium text-foreground">{info.imported_as}</span></div>
          <div>Source: <span className="font-medium text-foreground">{info.source}</span></div>
          <div>Updated: {new Date(info.timestamp).toLocaleString()}</div>
        </div>
      </div>
    );
  };
  
  // Render import relationships
  const renderImports = () => {
    if (!data || !data.imports || Object.keys(data.imports).length === 0) {
      return <p className="text-muted-foreground">No import relationships found</p>;
    }
    
    return (
      <div className="space-y-6">
        {Object.entries(data.imports).map(([varName, importers]) => (
          <div key={varName} className="space-y-2">
            <h3 className="font-medium text-lg flex items-center">
              Variable: <Badge variant="outline" className="ml-2">{varName}</Badge>
            </h3>
            {isMobile ? (
              // Mobile layout - cards
              <div className="space-y-2">
                {Object.entries(importers).map(([importerId, info]) => (
                  <ImportCard
                    key={importerId}
                    importerId={importerId}
                    info={info}
                  />
                ))}
              </div>
            ) : (
              // Desktop layout - table
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Importer</TableHead>
                    <TableHead>Imported As</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Last Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(importers).map(([importerId, info]) => (
                    <TableRow key={importerId}>
                      <TableCell className="font-medium">{importerId}</TableCell>
                      <TableCell>{info.imported_as}</TableCell>
                      <TableCell>{info.source}</TableCell>
                      <TableCell>{new Date(info.timestamp).toLocaleString()}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        ))}
      </div>
    );
  };
  
  return (
    <>
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <p>Loading variables...</p>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Global Variables */}
          <Card>
            <HierarchicalHeader
              title="Global Variables"
              level={2}
              isOpen={globalSectionOpen}
              onToggle={() => setGlobalSectionOpen(!globalSectionOpen)}
              count={data?.global ? Object.keys(data.global).length : 0}
              icon={<Globe className="h-4 w-4" />}
              actions={
                <Button 
                  variant="outline" 
                  size="sm" 
                  onClick={fetchRegistry}
                  disabled={loading}
                  title="Refresh variables"
                >
                  <RefreshCcw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
                </Button>
              }
            />
            {globalSectionOpen && (
              <HierarchicalContent level={2}>
                {renderGlobalVars()}
              </HierarchicalContent>
            )}
          </Card>

          {/* Group Variables */}
          <Card>
            <HierarchicalHeader
              title="Group Variables"
              level={2}
              isOpen={groupsSectionOpen}
              onToggle={() => setGroupsSectionOpen(!groupsSectionOpen)}
              count={data?.groups ? Object.keys(data.groups).length : 0}
              icon={<Users className="h-4 w-4" />}
            />
            {groupsSectionOpen && (
              <HierarchicalContent level={2}>
                {renderGroupVars()}
              </HierarchicalContent>
            )}
          </Card>

          {/* Import Relationships */}
          <Card>
            <HierarchicalHeader
              title="Import Relationships"
              level={2}
              isOpen={importsSectionOpen}
              onToggle={() => setImportsSectionOpen(!importsSectionOpen)}
              count={data?.imports ? Object.keys(data.imports).length : 0}
              icon={<FileText className="h-4 w-4" />}
            />
            {importsSectionOpen && (
              <HierarchicalContent level={2}>
                {renderImports()}
              </HierarchicalContent>
            )}
          </Card>
        </div>
      )}

      {/* Edit Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent className={isMobile ? "max-w-[95vw]" : ""}>
          <DialogHeader>
            <DialogTitle>Edit Variable</DialogTitle>
            <DialogDescription>
              Update the value of variable "{editingVar?.key}"
            </DialogDescription>
          </DialogHeader>
          
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label htmlFor="value" className="text-sm font-medium">
                Value
              </label>
              <textarea
                id="value"
                className="w-full min-h-[100px] p-2 border rounded-md bg-background text-sm font-mono"
                value={typeof editingVar?.value === 'string' ? editingVar.value : JSON.stringify(editingVar?.value, null, 2)}
                onChange={(e) => {
                  // Try to parse as JSON if it's a complex value, otherwise use as string
                  let newValue = e.target.value;
                  try {
                    if (e.target.value.startsWith('{') || e.target.value.startsWith('[') || 
                        e.target.value === 'true' || e.target.value === 'false' || 
                        !isNaN(Number(e.target.value))) {
                      newValue = JSON.parse(e.target.value);
                    }
                  } catch (error) {
                    // If parsing fails, use the raw string
                  }
                  setEditingVar(prev => prev ? { ...prev, value: newValue } : null);
                }}
              />
            </div>
          </div>
          
          <DialogFooter className={isMobile ? "flex-col gap-2" : ""}>
            <Button 
              variant="outline" 
              onClick={() => setEditDialogOpen(false)}
              className={isMobile ? "w-full" : ""}
            >
              Cancel
            </Button>
            <Button 
              onClick={handleSaveEdit}
              className={isMobile ? "w-full" : ""}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}; 