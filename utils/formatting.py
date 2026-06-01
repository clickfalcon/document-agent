import json
from pydantic import create_model, BaseModel, Field
from typing import List, Literal, Any, Dict, Optional
from agents.schema import BaseTaskOutput

TYPE_MAP = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "float": float
}

def parse_schema_to_fields(schema: Dict[str, Any]) -> Dict[str, Any]:
    fields = {}
    
    for key, info in schema.items():
        field_type_raw = info.get("type")
        field_desc = info.get("description", "")
        is_required = info.get("required", True)  # Default to True if omitted
        
        target_type: Any = None
        default_val: Any = ...  # Elipsis means required in Pydantic
        
        # 1. Handle standard primitives
        if isinstance(field_type_raw, str) and field_type_raw in TYPE_MAP:
            target_type = TYPE_MAP[field_type_raw]
            
        # 2. Handle Enums / Literals
        elif isinstance(field_type_raw, list):
            target_type = Literal[tuple(field_type_raw)]
            
        # 3. Handle Arrays of Nested Objects
        elif field_type_raw == "array" and "items" in info:
            nested_fields = parse_schema_to_fields(info["items"])
            nested_model = create_model(
                f"Dynamic{key.title().replace('_', '')}Item", 
                **nested_fields, 
                __base__=BaseModel
            )
            target_type = List[nested_model]
            if not is_required:
                default_val = Field(default_factory=list, description=field_desc)

        # Apply Optional logic if the field is not required
        if target_type and is_required is False and field_type_raw != "array":
            target_type = Optional[target_type]
            default_val = Field(None, description=field_desc)
        elif target_type and is_required is True and field_type_raw != "array":
            default_val = Field(..., description=field_desc)
            
        if target_type:
            fields[key] = (target_type, default_val)
            
    return fields

def build_response_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    # Generate the highly dynamic, production-ready class
    root_fields = parse_schema_to_fields(schema)
    return create_model("TaskOutput", **root_fields, __base__=BaseTaskOutput)

    # # --- VERIFY PARSING & VARIATION ---
    # # Test payload leaving out optional fields like "issues_found" and "suggested_action"
    # minimal_agent_response = {
    #     "is_compliant": True
    # }

    # # This parses perfectly now because optional handling is active!
    # validated_data = CodeComplianceOutput.model_validate(minimal_agent_response)
    # print(f"Validation successful! Is Compliant: {validated_data.is_compliant}")
    # print(f"Issues Found (defaulted): {validated_data.issues_found}")