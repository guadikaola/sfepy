"""
todo:
    - get row variable, col variable (if diff_var)
    - determine out shape
    - set current group to all variable arguments
    - loop over row/col dofs:
        - call term

? how to deal with components of (vector) variables?

(*) for given group, Variable has to be able to:
    - evaluate value in quadrature points
    - evaluate gradient in quadrature points
    - ? evaluate divergence in quadrature points
    - ? lazy evaluation, cache!

?? base function gradients in space elements stored now in terms - in
geometry, shared dict of geometries belongs to Equations
-> where to cache stuff? - in variables!
"""
import numpy as nm

from sfepy.base.base import output
from sfepy.terms.terms import Term
from sfepy.linalg import dot_sequences as dot

class NewTerm(Term):

    def get_shape_kind(self):
        if self.integration == 'surface':
            shape_kind = 'surface'

        elif self.integration in ('volume', 'surface_extra'):
            shape_kind = 'volume'

        else:
            raise NotImplementedError('unsupported term integration! (%s)'
                                      % self.integration)

        return shape_kind

    def assign_geometries(self, geometries):
        """
        Initialize the shared dict of geometries.
        """
        if self.has_geometry:
            self.geometries = geometries

        for var in self.get_variables():
            var.assign_geometries(geometries)

    def get_geometry_key(self, variable):
        is_trace = self.arg_traces[variable.name]
        geometry_type = self.geometry_types[variable.name]

        iname, region_name, ig = self.get_current_group()

        if is_trace:
            region, ig_map, ig_map_i = self.region.get_mirror_region()
            region_name = region.name
            ig = ig_map_i[ig]

        ap = variable.get_approximation(ig)
        key = (iname, region_name, geometry_type, ap.name)

        return key, ig

    def get_geometry(self, variable):
        key, ig = self.get_geometry_key(variable)

        if key in self.geometries:
            geo = self.geometries[key]

        else:
            geometry_type = key[2]
            geo = self.describe_geometry(variable, geometry_type, ig)
            self.geometries[key] = geo

            if geometry_type == 'surface_extra':
                key2 = list(key)
                key2[1] = 'surface'
                key2 = tuple(key2)
                self.geometries[key2] = geo

        return geo, key, ig

    def set_current_group(self, ig):
        """
        Set current group for the term and all variable in its
        arguments.
        """
        self.char_fun.set_current_group(ig)

        shape_kind = self.get_shape_kind()
        for var in self.get_variables():
            geo, geo_key, geo_ig = self.get_geometry(var)
            var.setup_bases(geo_key, geo_ig, geo, self.integral, shape_kind)
            var.set_current_group(geo_key, geo_ig)

    def integrate(self, val_qp, variable):
        shape_kind = self.get_shape_kind()

        geo, _, _ = self.get_geometry(variable)

        sh = val_qp.shape
        val = nm.zeros((sh[0], 1, sh[2], sh[3]), dtype=val_qp.dtype)
        if shape_kind == 'volume':
            chunk = self.region.cells[self.char_fun.ig]
            chunk = nm.arange(sh[0], dtype=nm.int32)
            geo.integrate_chunk(val, val_qp, chunk)

        else:
            lchunk = nm.arange(sh[0], dtype=nm.int32)
            geo.integrate_chunk(val, val_qp, lchunk)

        return val

    def evaluate(self, mode='eval', diff_var=None, **kwargs):
        shape_kind = self.get_shape_kind()

        if mode == 'eval':
            val = 0.0
            for ig in self.iter_groups():
                args = self.get_args(**kwargs)
                for aux, iels, status in self(*args,
                                              call_mode='d_eval', **kwargs):
                    val += self.sign * aux

        elif mode in ('el_avg', 'qp'):
            pass

        elif mode == 'weak':
            varr = self.get_virtual_variable()

            vals = []
            iels = []

            if diff_var is None:
                for ig in self.iter_groups():
                    args = self.get_args(**kwargs)

                    aux = varr.get_data_shape(ig, self.integral,
                                              shape_kind, self.region.name)
                    n_elr, n_qpr, dim, n_enr, n_cr = aux
                    n_row = n_cr * n_enr

                    shape = (n_elr, 1, n_row, 1)
                    val = nm.zeros(shape, dtype=varr.dtype)
                    for ir in varr.iter_dofs():
                        irs = slice(ir, ir + 1)

                        try:
                            val_qp = self(*args, **kwargs)

                        except ValueError:
                            output('%s term evaluation failed!' % self.name)
                            raise

                        _val = self.integrate(val_qp, varr)
                        val[..., irs, :] = _val

                    vals.append(self.sign * val)
                    iels.append((ig, nm.arange(n_elr, dtype=nm.int32)))

            else:
                varc = self.get_variables(as_list=False)[diff_var]

                for ig in self.iter_groups():
                    args = self.get_args(**kwargs)

                    aux = varr.get_data_shape(ig, self.integral,
                                              shape_kind, self.region.name)
                    n_elr, n_qpr, dim, n_enr, n_cr = aux
                    n_row = n_cr * n_enr

                    aux = varc.get_data_shape(ig, self.integral,
                                              shape_kind, self.region.name)
                    n_elc, n_qpc, dim, n_enc, n_cc = aux
                    n_col = n_cr * n_enr

                    shape = (n_elr, 1, n_row, n_col)
                    val = nm.zeros(shape, dtype=varr.dtype)
                    for ir in varr.iter_dofs():
                        irs = slice(ir, ir + 1)
                        for ic in varc.iter_dofs():
                            ics = slice(ic, ic + 1)
                            try:
                                val_qp = self(*args, **kwargs)

                            except ValueError:
                                output('%s term evaluation failed!' % self.name)
                                raise

                            _val = self.integrate(val_qp, varr)
                            val[..., irs, ics] = _val

                    vals.append(self.sign * val)
                    iels.append((ig, nm.arange(n_elr, dtype=nm.int32)))

        # Setup return value.
        if mode == 'eval':
            out = (vals,)

        else:
            out = (vals, iels)

        if len(out) == 1:
            out = out[0]

        # Hack: add zero status.
        return out + (0,)

class NewDiffusionTerm(NewTerm):
    """
    """
    name = 'dw_new_diffusion'
    arg_types = ('material', 'virtual', 'state')

    def __call__(self, mat, virtual, state, **kwargs):

        val = dot(virtual.grad(), dot(mat, state.grad()), 'ATB')

        return val