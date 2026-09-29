; The registered markdown grammar is the block one (tree-sitter-markdown); the
; inline grammar (emphasis, strong_emphasis, link_text, ...) is a separate
; parser jot does not load, and heading_content is a field name, not a node.
; Naming either made this whole query fail to compile, so markdown fell back to
; regex highlighting. Match only nodes the block grammar defines.
(atx_heading (inline) @keyword)
(setext_heading (paragraph) @keyword)
(code_fence_content) @string
(indented_code_block) @string
(fenced_code_block) @string
(link_destination) @string
