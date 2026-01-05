from Autodesk.Revit.DB import Options, Solid, GeometryInstance


def sample_element_uvw_points(elem, view, view_basis, cfg):
 """
 Tier-B proxy: sample geometry and return list of (u, v, w) points
 in view-space UVW.
 """
 opts = Options()
 opts.ComputeReferences = False
 opts.IncludeNonVisibleObjects = False
 opts.View = view

 geom = elem.get_Geometry(opts)
 if geom is None:
     return []

 pts = []

 def emit_point(p):
     uvw = view_basis.world_to_uvw(p)
     pts.append((uvw.u, uvw.v, uvw.w))

 for g in geom:
     if isinstance(g, GeometryInstance):
         inst_geom = g.GetInstanceGeometry()
         for ig in inst_geom:
             _sample_geom_object(ig, emit_point)
     else:
         _sample_geom_object(g, emit_point)

 return pts


def _sample_geom_object(obj, emit_point):
 # Faces / solids
 if isinstance(obj, Solid):
     if obj.Faces.Size == 0:
         return
     for face in obj.Faces:
         try:
             mesh = face.Triangulate()
         except Exception:
             continue
         for i in range(mesh.NumVertices):
             emit_point(mesh.get_Vertex(i))
     return

 # Curves
 try:
     curve = obj
     if hasattr(curve, "Tessellate"):
         for p in curve.Tessellate():
             emit_point(p)
 except Exception:
     pass
