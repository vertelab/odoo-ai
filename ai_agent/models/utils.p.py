import re


def graph_to_mermaid(graph):
    """
    Convert a LangGraph to Mermaid.js diagram format with proper Markdown and HTML support.

    Args:
        graph: The LangGraph object from get_graph()

    Returns:
        str: Mermaid.js formatted diagram text
    """
    # Extract nodes and edges from the graph
    nodes = graph.nodes
    edges = graph.edges

    # Create simplified node IDs for mermaid
    simplified_node_ids = {}
    counter = 0

    for node_id in nodes:
        # Create a simplified ID for Mermaid
        counter += 1
        simplified_id = f"node{counter}"
        simplified_node_ids[node_id] = simplified_id

    # Generate mermaid code with proper configuration
    mermaid_code = "%%{init: {'flowchart': {'curve': 'linear', 'htmlLabels': true}}}%%\ngraph TD;\n"

    # Add nodes with proper HTML/Markdown content
    for node_id in nodes:
        simplified_id = simplified_node_ids[node_id]

        if node_id == '__start__':
            mermaid_code += f"    {simplified_id}([Start]):::startNode;\n"
        elif node_id == '__end__':
            mermaid_code += f"    {simplified_id}([End]):::endNode;\n"
        else:
            # Process the multi-line node ID - preserve markdown and fa icons
            content = node_id

            # Convert fa&colon; to fa: for FontAwesome icons
            content = content.replace("fa&colon;", "fa:")

            # Ensure <small> tags are properly handled
            content = content.replace("<small>", "<span style='font-size:smaller'>").replace("</small>", "</span>")

            # Escape quotes
            content = content.replace('"', '\\"')

            # Use clickable node format with HTML content
            mermaid_code += f'    {simplified_id}["{content}"]:::customNode;\n'

    # Add edges
    for edge in edges:
        source_id = simplified_node_ids[edge.source]
        target_id = simplified_node_ids[edge.target]

        # Handle edge labels
        edge_label = ""
        if hasattr(edge, 'data') and edge.data not in [None, 'None']:
            # Clean the data string
            data_str = str(edge.data)
            # Remove quotes if present
            if data_str.startswith("'") and data_str.endswith("'"):
                data_str = data_str[1:-1]
            edge_label = f"|{data_str}|"

        if edge.conditional:
            mermaid_code += f"    {source_id} -.->{edge_label} {target_id};\n"
        else:
            mermaid_code += f"    {source_id} -->{edge_label} {target_id};\n"

    # Add CSS classes
    mermaid_code += """
    classDef startNode fill:#4CAF50,color:white,stroke:#388E3C,stroke-width:2px;
    classDef endNode fill:#F44336,color:white,stroke:#D32F2F,stroke-width:2px;
    classDef customNode fill:#E3F2FD,stroke:#2196F3,stroke-width:1px;
    """

    return mermaid_code