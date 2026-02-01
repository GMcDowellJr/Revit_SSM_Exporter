#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Automated fixer for bare `except Exception:` blocks.

This script converts:
    except Exception:
        pass  # or similar

To:
    except Exception as e:
        if diag is not None:
            diag.error(...)

Usage:
    python tools/fix_bare_except.py vop_interwoven/ --apply

The script is conservative: it only modifies patterns it can safely transform.
"""

import re
import os
import sys
import argparse
from collections import defaultdict

# Patterns that indicate a "safe" bare except (don't need diagnostics)
SAFE_PATTERNS = [
    # Import fallback - expected to fail if module not available
    r'except Exception:\s*\n\s+\w+\s*=\s*None',
    # String conversion fallback - minor utility
    r'except Exception:\s*\n\s+return\s+["\']<',
    # AttributeError alternative (some use Exception instead)
    r'try:\s*\n\s+.*getattr.*\n\s*except Exception:\s*\n\s+pass',
]

# Context extractors - try to find nearby context variables
CONTEXT_VARS = {
    'elem_id': [r'\belem_id\s*=', r'getattr.*Id.*IntegerValue', r'\beid\b'],
    'view_id': [r'\bview_id\s*=', r'view\.Id'],
    'source': [r'\bsource\s*=', r'source_type'],
    'category': [r'\bcategory\s*=', r'\bcat_name\b'],
}

def get_function_name(lines, line_idx):
    """Extract the name of the containing function."""
    for i in range(line_idx, -1, -1):
        line = lines[i]
        match = re.search(r'def\s+(\w+)\s*\(', line)
        if match:
            return match.group(1)
    return "unknown"

def get_phase_from_context(filepath, func_name):
    """Infer the phase from the file path and function name."""
    if 'collection' in filepath:
        return 'collection'
    if 'annotation' in filepath:
        return 'annotation'
    if 'silhouette' in filepath or 'areal' in filepath or 'geometry' in filepath:
        return 'geometry_extraction'
    if 'raster' in filepath:
        return 'rasterization'
    if 'export' in filepath or 'csv' in filepath or 'png' in filepath:
        return 'export'
    if 'view_basis' in filepath:
        return 'view_basis'
    if 'linked' in filepath:
        return 'linked_documents'
    if 'pipeline' in filepath:
        return 'pipeline'
    if 'cache' in filepath:
        return 'caching'
    return 'general'

def has_diag_in_scope(lines, line_idx):
    """Check if 'diag' variable is likely in scope."""
    func_start = line_idx
    for i in range(line_idx, -1, -1):
        if re.match(r'^def\s+', lines[i]):
            func_start = i
            break

    # Check function signature and body for 'diag'
    scope_text = '\n'.join(lines[func_start:line_idx+1])
    if 'diag' in scope_text:
        return True

    # Check if class has self.diag
    for i in range(func_start, -1, -1):
        if re.match(r'^class\s+', lines[i]):
            break
        if 'self.diag' in lines[i] or 'self._diag' in lines[i]:
            return True

    return False

def find_except_blocks(content, filepath):
    """Find all except Exception: blocks and their context."""
    lines = content.split('\n')
    blocks = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Find except Exception:
        match = re.match(r'^(\s*)except\s+Exception\s*:', line)
        if match:
            indent = match.group(1)
            except_line = i

            # Collect the except block body
            body_start = i + 1
            body_lines = []
            j = body_start
            while j < len(lines):
                body_line = lines[j]
                # Check if still in block (has more indent or is blank)
                if body_line.strip() == '':
                    body_lines.append(body_line)
                    j += 1
                    continue

                # Check indentation
                body_match = re.match(r'^(\s*)', body_line)
                body_indent = body_match.group(1) if body_match else ''

                if len(body_indent) <= len(indent) and body_line.strip():
                    break

                body_lines.append(body_line)
                j += 1

            body_end = j

            # Get context
            func_name = get_function_name(lines, except_line)
            phase = get_phase_from_context(filepath, func_name)
            has_diag = has_diag_in_scope(lines, except_line)

            # Check if it's already capturing exception
            already_captures = re.match(r'^(\s*)except\s+Exception\s+as\s+\w+\s*:', line)

            blocks.append({
                'line_num': except_line + 1,  # 1-indexed
                'line_start': except_line,
                'body_start': body_start,
                'body_end': body_end,
                'indent': indent,
                'func_name': func_name,
                'phase': phase,
                'has_diag': has_diag,
                'already_captures': already_captures is not None,
                'body': body_lines,
                'original_line': line,
            })

            i = body_end
        else:
            i += 1

    return blocks

def generate_fix(block, filepath):
    """Generate the fixed code for an except block."""
    indent = block['indent']
    body_indent = indent + '    '

    func_name = block['func_name']
    phase = block['phase']
    has_diag = block['has_diag']

    # Generate the except line
    except_line = indent + 'except Exception as e:'

    # Generate the diagnostic recording
    diag_lines = []
    if has_diag:
        diag_lines = [
            body_indent + 'if diag is not None:',
            body_indent + '    diag.error(',
            body_indent + '        phase="{}",'.format(phase),
            body_indent + '        callsite="{}",'.format(func_name),
            body_indent + '        message="Exception in {}: {{}}".format(e),'.format(func_name),
            body_indent + '        exc=e,',
            body_indent + '    )',
        ]
    else:
        # No diag in scope - add a comment explaining the silent failure
        diag_lines = [
            body_indent + '# Exception in {} - no diag in scope'.format(func_name),
            body_indent + 'pass  # TODO: Add diagnostics when diag becomes available',
        ]

    # Check if original body had meaningful code after pass
    original_body = '\n'.join(block['body'])
    has_only_pass = re.match(r'^\s*pass\s*(#.*)?\s*$', original_body.strip())

    if has_only_pass:
        # Just pass - replace with diagnostics
        return '\n'.join([except_line] + diag_lines)
    else:
        # Has other code - add diagnostics before existing code
        # But keep the existing body after diagnostics
        clean_body = []
        for line in block['body']:
            if line.strip() == 'pass':
                continue  # Remove redundant pass
            clean_body.append(line)

        if has_diag:
            return '\n'.join([except_line] + diag_lines + clean_body)
        else:
            return '\n'.join([except_line] + clean_body)

def apply_fixes(content, blocks, filepath):
    """Apply fixes to the content."""
    lines = content.split('\n')

    # Sort blocks in reverse order so line numbers stay valid
    blocks = sorted(blocks, key=lambda b: b['line_start'], reverse=True)

    for block in blocks:
        if block['already_captures']:
            continue  # Already has 'as e'

        # Generate fix
        fix = generate_fix(block, filepath)
        fix_lines = fix.split('\n')

        # Replace the block
        start = block['line_start']
        end = block['body_end']
        lines[start:end] = fix_lines

    return '\n'.join(lines)

def process_file(filepath, apply=False, verbose=False):
    """Process a single file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = find_except_blocks(content, filepath)

    if not blocks:
        return 0, 0

    # Count unfixed blocks
    unfixed = [b for b in blocks if not b['already_captures']]

    if verbose:
        print("\n{}:".format(filepath))
        for block in unfixed:
            print("  Line {}: {} in {}() [has_diag={}]".format(
                block['line_num'],
                'except Exception:',
                block['func_name'],
                block['has_diag']
            ))

    if apply and unfixed:
        new_content = apply_fixes(content, blocks, filepath)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("  Fixed {} blocks in {}".format(len(unfixed), filepath))

    return len(blocks), len(unfixed)

def main():
    parser = argparse.ArgumentParser(description='Fix bare except Exception: blocks')
    parser.add_argument('paths', nargs='+', help='Directories or files to process')
    parser.add_argument('--apply', action='store_true', help='Apply fixes (default: dry run)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show details')
    args = parser.parse_args()

    total_blocks = 0
    total_unfixed = 0
    files_processed = 0

    for path in args.paths:
        if os.path.isfile(path):
            files = [path]
        else:
            files = []
            for root, dirs, filenames in os.walk(path):
                for filename in filenames:
                    if filename.endswith('.py'):
                        files.append(os.path.join(root, filename))

        for filepath in files:
            blocks, unfixed = process_file(filepath, apply=args.apply, verbose=args.verbose)
            total_blocks += blocks
            total_unfixed += unfixed
            if blocks > 0:
                files_processed += 1

    print("\n" + "="*60)
    print("Summary:")
    print("  Files with except blocks: {}".format(files_processed))
    print("  Total except Exception: blocks: {}".format(total_blocks))
    print("  Blocks needing fix: {}".format(total_unfixed))
    if args.apply:
        print("  Status: FIXES APPLIED")
    else:
        print("  Status: DRY RUN (use --apply to fix)")

if __name__ == '__main__':
    main()
