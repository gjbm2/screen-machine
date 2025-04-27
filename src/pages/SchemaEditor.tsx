import React, { useState, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { JsonForms } from '@jsonforms/react';
import { materialRenderers, materialCells } from '@jsonforms/material-renderers';
import { toast } from 'sonner';
import { Generate, ControlProps, ControlState, isControl, RankedTester, rankWith, JsonSchema } from '@jsonforms/core';

// General type inference renderer
const TypeInferenceControl = (props: ControlProps) => {
  const { schema } = props;
  
  // If schema has anyOf/oneOf, use the first non-boolean type
  if (schema.anyOf || schema.oneOf) {
    const types = schema.anyOf || schema.oneOf;
    const nonBooleanType = types.find((type: any) => type.type !== 'boolean');
    if (nonBooleanType) {
      return materialRenderers[0].renderer({ ...props, schema: nonBooleanType });
    }
  }
  
  // Default to material renderer
  return materialRenderers[0].renderer(props);
};

const typeInferenceTester: RankedTester = rankWith(
  10,
  (uischema, schema: JsonSchema) => {
    return isControl(uischema) && Boolean(schema.anyOf || schema.oneOf);
  }
);

const SchemaEditor: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  
  let initialState;
  try {
    if (location.state) {
      initialState = location.state;
    } else {
      const params = new URLSearchParams(location.search);
      const stateParam = params.get('state');
      if (!stateParam) {
        throw new Error('No schema provided');
      }
      // stateParam is expected to be base64url-encoded JSON
      const urlDecoded = decodeURIComponent(stateParam);
      const base64 = urlDecoded.replace(/-/g, '+').replace(/_/g, '/');
      const padded = base64.padEnd(base64.length + (4 - (base64.length % 4)) % 4, '=');
      const decodedState = atob(padded);
      console.debug('Decoded state string:', decodedState);
      initialState = JSON.parse(decodedState);
    }
    console.log('Initial state:', initialState);
  } catch (error) {
    console.error('Failed to parse state:', error);
    return <div>Error: Failed to parse schema from URL</div>;
  }

  const { schema, currentData: initialData, returnUrl, saveEndpoint } = initialState;
  const [formData, setFormData] = useState(initialData || {});
  const [errors, setErrors] = useState<any[]>([]);
  const [tab, setTab] = useState<'editor' | 'raw'>('editor');
  const [rawJson, setRawJson] = useState(() => JSON.stringify(formData, null, 2));
  const [rawValid, setRawValid] = useState(true);
  const [showSchemaModal, setShowSchemaModal] = useState(false);

  if (!schema) {
    return <div>Error: No schema provided</div>;
  }
  
  if (!saveEndpoint) {
    return <div>Error: No save endpoint provided</div>;
  }

  const { saveMethod = 'PUT' } = initialState;

  // Helper to resolve $ref recursively in schema
  function resolveRefRecursive(schema, ref) {
    // Only supports local refs like "#/definitions/instruction"
    const path = ref.replace(/^#\//, '').split('/');
    let current = schema;
    for (const part of path) {
      current = current[part];
      if (!current) return undefined;
    }
    // If the resolved object is itself a $ref, resolve again
    if (current && current.$ref) {
      return resolveRefRecursive(schema, current.$ref);
    }
    return current;
  }

  // Helper to build a detail layout for an array of objects, robust to $ref chains
  function buildArrayDetail(schema, arrayProp) {
    let itemsSchema;
    // 1. Direct property
    if (schema.properties && schema.properties[arrayProp]) {
      const prop = schema.properties[arrayProp];
      if (prop.items) {
        itemsSchema = prop.items;
      } else if (prop.$ref) {
        // $ref at the property level
        const refSchema = resolveRefRecursive(schema, prop.$ref);
        if (refSchema && refSchema.items) {
          itemsSchema = refSchema.items;
        }
      }
    }
    // $ref at the array level
    if (!itemsSchema && schema.$ref) {
      const refSchema = resolveRefRecursive(schema, schema.$ref);
      if (refSchema && refSchema.items) {
        itemsSchema = refSchema.items;
      }
    }
    // $ref at the items level
    if (itemsSchema && itemsSchema.$ref) {
      itemsSchema = resolveRefRecursive(schema, itemsSchema.$ref);
    }
    if (!itemsSchema || !itemsSchema.properties) {
      return {
        type: 'VerticalLayout',
        elements: [
          { type: 'Label', text: 'No properties found for this array.' }
        ]
      };
    }

    return {
      type: 'VerticalLayout',
      elements: Object.keys(itemsSchema.properties).map(key => {
        const control = {
          type: 'Control',
          scope: `#/properties/${key}`
        };

        // Special handling for value field to ensure proper rendering
        if (key === 'value') {
          return {
            ...control,
            options: {
              format: 'text'
            }
          };
        }

        return control;
      })
    };
  }

  // Build the schedule UI schema dynamically
  const buildScheduleUiSchema = (schema) => ({
    type: 'VerticalLayout',
    elements: [
      {
        type: 'Control',
        label: 'Instructions',
        scope: '#/properties/instructions',
        options: {
          detail: buildArrayDetail(schema, 'instructions')
        }
      },
      {
        type: 'Control',
        label: 'Triggers',
        scope: '#/properties/triggers',
        options: {
          detail: {
            type: 'VerticalLayout',
            elements: [
              { type: 'Control', scope: '#/properties/type' },
              { type: 'Control', scope: '#/properties/value' },
              { type: 'Control', scope: '#/properties/repeat' },
              { type: 'Control', scope: '#/properties/window' },
              { type: 'Control', scope: '#/properties/important' },
              { type: 'Control', scope: '#/properties/urgent' },
              {
                type: 'Control',
                label: 'Nested Instructions',
                scope: '#/properties/instructions',
                options: {
                  detail: buildArrayDetail(schema, 'instructions')
                }
              }
            ]
          }
        }
      }
    ]
  });

  let uiSchema;
  if (schema && schema.properties && schema.properties.triggers) {
    uiSchema = buildScheduleUiSchema(schema);
  } else {
    uiSchema = Generate.uiSchema(schema);
  }

  // When switching to editor, update formData if raw is valid and changed
  const handleTabChange = (newTab) => {
    if (newTab === 'editor' && rawValid) {
      try {
        const parsed = JSON.parse(rawJson);
        setFormData(parsed);
      } catch {}
    }
    setTab(newTab);
  };

  // When formData changes, update rawJson (if not on raw tab)
  React.useEffect(() => {
    if (tab === 'editor') {
      setRawJson(JSON.stringify(formData, null, 2));
    }
  }, [formData, tab]);

  // When rawJson changes, validate
  React.useEffect(() => {
    try {
      JSON.parse(rawJson);
      setRawValid(true);
    } catch {
      setRawValid(false);
    }
  }, [rawJson]);

  // Get schema title/description or fallback
  const schemaTitle = schema.title || 'Untitled Schema';
  const schemaDescription = schema.description || '';

  // Try to get a file name from the $schema or fallback
  let schemaFileName = 'schema.json';
  if (schema.$schema && typeof schema.$schema === 'string') {
    const match = schema.$schema.match(/\/([^\/]+)\.json/);
    if (match) schemaFileName = match[1] + '.json';
  }

  return (
    <div className="flex flex-col h-full min-h-screen">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-2 p-4 border-b bg-white sticky top-0 z-10">
        <div>
          <div className="text-xl font-bold">{schemaTitle}</div>
          <div className="text-gray-600 text-sm">{schemaDescription || schemaFileName}</div>
        </div>
        <button
          className="text-blue-600 underline text-sm mt-2 md:mt-0"
          onClick={() => setShowSchemaModal(true)}
        >
          View Raw Schema
        </button>
      </div>
      {/* Tabs */}
      <div className="flex gap-2 border-b px-4 bg-white sticky top-0 z-20">
        <button
          className={`px-4 py-2 ${tab === 'editor' ? 'border-b-2 border-blue-600 font-bold' : ''}`}
          onClick={() => handleTabChange('editor')}
        >
          Editor
        </button>
        <button
          className={`px-4 py-2 ${tab === 'raw' ? 'border-b-2 border-blue-600 font-bold' : ''}`}
          onClick={() => handleTabChange('raw')}
        >
          Raw
        </button>
        {tab === 'raw' && (
          <span className="ml-2 flex items-center">
            {rawValid ? (
              <span title="Valid JSON" className="text-green-600">✔</span>
            ) : (
              <span title="Invalid JSON" className="text-red-600">✖</span>
            )}
          </span>
        )}
      </div>
      {/* Main content area */}
      <div className="flex-1 flex flex-col overflow-y-auto">
        {tab === 'editor' ? (
          <div className="flex-1 overflow-y-auto p-4">
            <JsonForms
              schema={schema}
              uischema={uiSchema}
              data={formData}
              renderers={materialRenderers}
              cells={materialCells}
              onChange={({ data, errors }) => {
                console.log('Form data changed:', data);
                console.log('Validation errors:', errors);
                setFormData(data);
                setErrors(errors || []);
              }}
              config={{
                restrict: false,
                trim: false,
                showUnfocusedDescription: true,
                hideRequiredAsterisk: false
              }}
            />
          </div>
        ) : (
          <div className="flex-1 flex flex-col p-4">
            <textarea
              className="w-full flex-1 min-h-[300px] max-h-[70vh] p-2 border rounded font-mono text-sm resize-vertical"
              style={{ minHeight: 200 }}
              value={rawJson}
              onChange={e => setRawJson(e.target.value)}
              spellCheck={false}
            />
          </div>
        )}
      </div>
      {/* Save/Cancel always visible */}
      <div className="flex justify-end gap-2 p-4 border-t bg-white sticky bottom-0 z-10">
        <button
          type="button"
          onClick={() => {
            if (returnUrl) {
              navigate(returnUrl);
            }
          }}
          className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md shadow-sm hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => {
            (async () => {
              if (errors.length === 0) {
                try {
                  const response = await fetch(saveEndpoint, {
                    method: saveMethod,
                    headers: {
                      'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(formData),
                  });
                  if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.error || 'Failed to save data');
                  }
                  toast.success('Changes saved successfully');
                  if (returnUrl) {
                    navigate(returnUrl);
                  }
                } catch (error) {
                  toast.error(error.message || 'Failed to save changes');
                }
              } else {
                toast.error('Please fix validation errors before saving');
              }
            })();
          }}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md shadow-sm hover:bg-blue-700"
          disabled={errors.length > 0 || (tab === 'raw' && !rawValid)}
        >
          Save Changes
        </button>
      </div>
      {/* Schema Modal */}
      {showSchemaModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40">
          <div className="bg-white rounded shadow-lg max-w-2xl w-full p-6 relative">
            <button
              className="absolute top-2 right-2 text-gray-500 hover:text-gray-800"
              onClick={() => setShowSchemaModal(false)}
            >
              ✖
            </button>
            <div className="font-bold mb-2">Raw Schema</div>
            <pre className="overflow-x-auto text-xs bg-gray-100 p-2 rounded max-h-[60vh]">
              {JSON.stringify(schema, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

export default SchemaEditor; 