from scipy.optimize import curve_fit
from other_functions import *
import numpy as np
import tifffile
import os

class Lens:
    """Class containing lens parameters

    Parameters
    ----------
    NA : Float
        Numerical aperture of lens
    RI : Float
        Refractive index of the image space
    rot : Float
        Rotation of the lens around the x-axis

    Attributes
    ----------
    NA
    RI
    rot

    """

    def __init__(self,NA,RI,rot):
        self.NA = NA    #Numerical aperture
        self.RI = RI    #Refractive index
        self.rot = rot  #Rotation of lens


class Camera:
    """Class containin camera parameters

    Parameters
    ----------
    res : Integer
        Number of pixels on the camera chip in each axis
    vox : Float
        Distance between sampling points on camera chip

    Attributes
    ----------
    res
    vox

    """

    def __init__(self,res,vox,offset,RMS):
        self.res = res #Field of view [pixels]
        self.vox = vox #Voxel size [m]
        self.offset = offset
        self.RMS = RMS


class Microscope:
    """Class containing all simulation functions

    Parameters
    ----------
    lam_ex : Float
        Excitation wavelength
    lam_em : type
        Emission wavelength

    Attributes
    ----------
    lenses : List
        List of all lenses contained in the system
    k0 : Float
        Wavenumber of emission light
    lam_ex
    lam_em

    """
    def __init__(self,lam_ex,lam_em,path):
        self.lenses = []              #List of all lenses in system
        self.lam_ex = lam_ex          #Excitation wavelength [m]
        self.lam_em = lam_em          #Emission wavelength [m]
        self.k0 = 2*np.pi/self.lam_em #Emission wavenumber [rad/m]
        self.path = path              #Save folder location

    def add_lens(self,NA,RI=1,rot=0,pos=False):
        """Adds lens to the system

        Parameters
        ----------
        NA : Float
            Numerical aperture of the lens
        RI : Float
            Refractive index in the image space
        rot : Float
            Rotation of the lens around the x-axis
        pos : Int
            Position of the lens in the setup

        """
        lens = Lens(NA,RI,rot)
        if pos == False:
            self.lenses.append(lens)
        elif not type(pos) is int:
            raise TypeError('Lens position can only be integer')
        elif len(self.lenses) < pos:
            raise ValueError('Lens position out of range')
        else:
            self.lenses.insert(pos,lens)

    def add_camera(self,res,vox,offset,RMS):
        """Updates the camera of the system

        Parameters
        ----------
        res : Integer
            Number of pixels on the camera chip in each axis
        vox : Float
            Distance between sampling points on camera chip

        """
        if not type(res) is int:
            raise TypeError('Pixel count can only be integer')
        else:
            self.camera = Camera(res,vox,offset,RMS)

    def calculate_system_specs(self):
        """Calculates the system specifications

        Specs
        -------
        mag
            Transverse magnification
        alpha
            Rotation of the sample plane
        axial_mag
            Axial magnification
        z_voxel_size
            z-sampling in image space to get uniform
            sampling of the object space
        scaling
            Scaling constant used for the electric field evaluation

        """
        #Calculates system magnification and light-sheet rotation
        self.mag = 1
        self.alpha = np.pi/2-np.sum([lens.rot for lens in self.lenses])
        for i,lens in enumerate(self.lenses):
            if i % 2 == 0:
                self.mag *= lens.NA
            else:
                self.mag /= lens.NA
        self.axial_mag = self.mag**2*self.lenses[-1].RI/self.lenses[0].RI
        self.z_voxel_size = self.camera.vox/self.mag*self.axial_mag
        self.scaling = self.lam_em/(2*self.camera.vox*self.lenses[-1].NA)

        #Clculates system FoV in sample space
        if hasattr(self, 'camera'):
            self.FoV = self.camera.vox*self.camera.res/self.mag

    def light_sheet(self):
        """Calculates the light-sheet of the system. The light-sheet is assumed
           to be generated by uniform polarized input light entering the back
           focal plane of the illumination objective. The light is passed
           through a mask and focused by the objective.

        """

        #Defines the resolution of the electric field
        res = self.camera.res
        M = res//2
        x = y = np.linspace(-M,M,res)
        xx,yy = np.meshgrid(x,y)
        RR = np.sqrt(xx**2+yy**2)

        #Calculates the wavenumber proportiones by the NA of the light-sheet
        NA = self.lenses[0].RI*np.sin(self.ls_opening)
        del_K = self.k0*NA/M
        k_xy = del_K*RR
        k_z = np.sqrt((self.k0*self.lenses[0].RI)**2 - k_xy**2)

        #Defines spherical coordinates of the lens
        theta = np.arcsin((del_K/(self.k0*self.lenses[0].RI))*RR)
        phi = np.arctan2(yy,xx)

        #Defines the initial electric field in the fourier space of the lens
        if self.ls_pol == 'p':
            self.Ei_base = np.array((1,0,0))
        elif self.ls_pol == 's':
            self.Ei_base = np.array((0,1,0))
        elif self.ls_pol == 'u':
            self.Ei_base = np.array((np.sqrt(2)/2,np.sqrt(2)/2,0))
        Ei = np.ones((res,res,3))*self.Ei_base

        #Defines the mask shaping the light in the back focal plane and
        #multiplies it with the initial electric field
        mask = np.ones_like(theta)
        mask[theta>self.ls_opening] = np.NaN
        mask[:,:M] = np.NaN
        mask[:,M+1:] = np.NaN
        Ei *= mask.reshape(res,res,1)

        #Calculates the coordinate transform the lens induces
        #and transform the electric field
        jones_mat = [np.linalg.inv(R_z(phi))]
        jones_mat.append(L_refraction(-theta))
        jones_mat.append(R_z(phi))
        jones_mat = np.array(jones_mat)
        transform = multidot(jones_mat)
        Ef = np.nan_to_num(np.einsum('abji,abi->abj', transform, Ei))

        #Defines the back aperture obliqueness of the electric field
        bao = np.nan_to_num(1 / np.cos(theta))

        #Calculate the z-sampling to get uniform sampling of the object space
        z_max = res//2 * self.camera.vox/self.mag
        z_val = np.linspace(-z_max,z_max,res)

        #Performs the Debye integral to evaluate the
        #electric field (usong a Fourier transform)
        scaling = self.lam_ex/(2*self.camera.vox/self.mag*NA)
        k_z = np.nan_to_num(k_z)
        self.ls_PSF = dft2_volume(Ef,k_z,z_val,bao,res,scaling).transpose(1,2,0)

    def field_tracing_reverse(self):
        """Ray traces the system and calculates the transformation. The ray
           trace starts at the last lens of the system and moves towards the
           first. This is done so we can define the sampling in the image space
           to be uniform, and thus use a fast field evaluation using a fourier
           transform.

        """
        #Defines the resolution of the electric field trace
        M = self.camera.res//2
        x = y = np.linspace(-M,M,self.camera.res)
        xx,yy = np.meshgrid(x,y)
        RR = np.sqrt(xx**2+yy**2)

        #Makes a list of lenses starting at the last lens of the system
        #and define the rotation direction (defined by the direction of the
        #lens focus in the z-axis)
        lenses = []
        rotation_direction = np.ones(len(self.lenses),dtype=np.uint8)*-1
        for i in range(1,len(self.lenses)+1):
            lenses.append(self.lenses[-i])
            rotation_direction[-i] *= (-1)**((i+1)//2+1)

        #Calculates the wavenumber proportiones by the NA of the last lens
        del_K = self.k0*lenses[0].NA/M

        #Iterates through the lenses and append the coordinate transform
        #of the lens to a list
        modes = ['collimating','focusing']
        apodization = np.ones((self.camera.res,self.camera.res))
        for i,lens in enumerate(lenses):
            #Determines if the lens is focusing or collimating
            mode = modes[(i+1)%2]
            dir = rotation_direction[i]

            if i == 0:
                #Special case for the first lens in the system, creating the
                #transformation list and the spherical coordinates of the lenses
                lens.theta = np.arcsin((del_K/(self.k0*lens.RI))*RR)
                lens.phi = np.arctan2(yy,xx)
                jones_mat = [np.linalg.inv(R_z(lens.phi))]
                jones_mat.append(L_refraction(dir*lens.theta))
                apodization *= np.sqrt(lens.RI*np.cos(lens.theta))

            elif mode == 'collimating':
                #If the lens is collimating, a lens transformation is added to
                #the transform list
                lens.theta = np.arcsin(lens.NA*tmp_lens.RI*np.sin(tmp_lens.theta)/(lens.RI*tmp_lens.NA))
                lens.phi = tmp_lens.phi
                jones_mat.append(L_refraction(dir*lens.theta))
                apodization /= np.sqrt(lens.RI*np.cos(lens.theta))

            elif mode == 'focusing':
                #If the lens is focusing, there are three cases:
                #
                #1) There is a rotation between the focusing and collimating
                #   optical axes. And the potential refractive index change is
                #   orthogonal to the focusing lens optical axis
                #
                #2) There is a rotation between the focusing and collimating
                #   optical axes. And the potential refractive index change is
                #   orthogonal to the collimating lens optical axis
                #
                #3) There is no rotation between the focusing and collimating
                #   optical axes. And the potential refractiv index change is
                #   orthogonal to the optical axis
                #
                #In all cases, there can be a refractive index change between
                #the two lenses.
                if not lens.rot == 0: #Case 1)
                    Rx = R_x(-lens.rot)
                    ki = np.array((np.sin(tmp_lens.theta)*np.cos(tmp_lens.phi),
                                   np.sin(tmp_lens.theta)*np.sin(tmp_lens.phi),
                                   np.cos(tmp_lens.theta)))
                    kf = np.einsum('ij,jkl->ikl',Rx,ki)
                    phi = (np.arctan2(kf[1], kf[0]))
                    theta = np.arctan2(np.sqrt(kf[0]**2+kf[1]**2),kf[2])
                    theta[kf[2]<0] = np.NaN

                    jones_mat.append(R_z(tmp_lens.phi))
                    Rx_mat = np.broadcast_to(R_x(lens.rot),(self.camera.res,self.camera.res,3,3))
                    jones_mat.append(Rx_mat)
                    jones_mat.append(np.linalg.inv(R_z(phi)))

                    lens.theta = np.arcsin(tmp_lens.RI*np.sin(theta)/lens.RI)
                    lens.phi = phi
                    jones_mat.append(R_y(-1*dir*theta))
                    jones_mat.append(Fresnel(lens.theta,theta,lens.RI,tmp_lens.RI))
                    jones_mat.append(R_y(dir*lens.theta))
                    jones_mat.append(L_refraction(dir*lens.theta))

                elif not tmp_lens.rot == 0: #Case 2)
                    theta = np.arcsin(tmp_lens.RI*np.sin(tmp_lens.theta)/lens.RI)
                    phi = tmp_lens.phi
                    jones_mat.append(R_y(-1*dir*tmp_lens.theta))
                    jones_mat.append(Fresnel(theta,tmp_lens.theta,lens.RI,tmp_lens.RI))
                    jones_mat.append(R_y(dir*theta))

                    Rx = R_x(-tmp_lens.rot)
                    ki = np.array((np.sin(theta)*np.cos(phi),
                                   np.sin(theta)*np.sin(phi),
                                   np.cos(theta)))
                    kf = np.einsum('ij,jkl->ikl',Rx,ki)
                    lens.phi = (np.arctan2(kf[1], kf[0]))
                    lens.theta = np.arctan2(np.sqrt(kf[0]**2+kf[1]**2),kf[2])
                    lens.theta[kf[2]<0] = np.NaN

                    jones_mat.append(R_z(phi))
                    Rx_mat = np.broadcast_to(R_x(tmp_lens.rot),(self.camera.res,self.camera.res,3,3))
                    jones_mat.append(Rx_mat)
                    jones_mat.append(np.linalg.inv(R_z(lens.phi)))
                    jones_mat.append(L_refraction(dir*lens.theta))

                else: #Case 3)
                    lens.theta = np.arcsin(tmp_lens.RI*np.sin(tmp_lens.theta)/lens.RI)
                    lens.phi = tmp_lens.phi
                    jones_mat.append(R_y(-1*dir*tmp_lens.theta))
                    jones_mat.append(Fresnel(lens.theta,tmp_lens.theta,lens.RI,tmp_lens.RI))
                    jones_mat.append(R_y(dir*lens.theta))
                    jones_mat.append(L_refraction(dir*lens.theta))

                apodization *= np.sqrt(lens.RI*np.cos(lens.theta))

            #The lens that was iterated through is added as a temporary lens
            #to be available during the next iteration
            th_max = np.arcsin(lens.NA/lens.RI)
            lens.theta[lens.theta>th_max] = np.NaN
            tmp_lens = lens

        #Calculates the transform matrix from the list of transform matrices.
        #The final field can then be calculated using: E_f = T * E_i
        #Where E_f is the final field, T is the transform matrix, * is a dot
        #product and E_i is the initial field
        jones_mat.append(R_z(lenses[-1].phi))
        self.jones_mat = np.array(jones_mat)
        self.transform = multidot(self.jones_mat)
        self.apodization = apodization            

    def make_MTF(self):
        """Calculates the MTF of the system using a Fourier transform.

        """
        #Zero pads the effective PSF to the desired OTF resolution to get the
        #desired base frequency
        padding = (self.OTF_res-self.camera.res)//2
        product = np.pad(self.eff_PSF,padding)

        #Adds noise to the PSF
        if self.SNR != 0: #zero SNR is defined as no noise
            product, poisson = add_noise(product,self.SNR**2,self.camera.offset,self.camera.RMS)

        #Snips out the effective PSF with noise to save
        self.PSF_poisson = poisson[padding:padding+self.camera.res,
                                   padding:padding+self.camera.res,
                                   padding:padding+self.camera.res]
        self.PSF_readout = product[padding:padding+self.camera.res,
                                   padding:padding+self.camera.res,
                                   padding:padding+self.camera.res]

        #Fourier transform the effective PSF to get the MTF
        OTF_noiseless = np.fft.fftshift(np.fft.fftn(np.fft.fftshift(product)))
        OTF_poisson = np.fft.fftshift(np.fft.fftn(np.fft.fftshift(poisson)))
        OTF_readout = np.fft.fftshift(np.fft.fftn(np.fft.fftshift(product)))
        self.MTF_noiseless = np.abs(OTF_noiseless)
        self.MTF_poisson = np.abs(OTF_poisson)
        self.MTF_readout = np.abs(OTF_readout)

        #Calculates the base frequency of the MTF
        self.base_freq = 1/(len(self.MTF_readout)*(self.camera.vox/(self.mag)))

    def save_stacks(self):
        if not os.path.exists(self.path):
            os.mkdir(self.path)
        with tifffile.TiffWriter(self.path+'/PSF.tiff') as stack:
            stack.save(img16(self.PSF).transpose(2,1,0),contiguous=True)
        with tifffile.TiffWriter(self.path+'/PSF_effective.tiff') as stack:
            stack.save(img16(self.eff_PSF).transpose(2,1,0),contiguous=True)
        with tifffile.TiffWriter(self.path+'/PSF_poisson.tiff') as stack:
            stack.save(self.PSF_poisson.transpose(2,1,0),contiguous=True)
        with tifffile.TiffWriter(self.path+'/PSF_readout.tiff') as stack:
            stack.save(self.PSF_readout.transpose(2,1,0),contiguous=True)
        with tifffile.TiffWriter(self.path+'/MTF_noiseless.tiff') as stack:
            stack.save(self.MTF_noiseless.transpose(2,1,0),contiguous=True)
        with tifffile.TiffWriter(self.path+'/MTF_poisson.tiff') as stack:
            stack.save(self.MTF_poisson.transpose(2,1,0),contiguous=True)
        with tifffile.TiffWriter(self.path+'/MTF_readout.tiff') as stack:
            stack.save(self.MTF_readout.transpose(2,1,0),contiguous=True)

    def analyze(self):
            """Automated analysis script used to extract resolution from the MTF.

            """
            #Define the half length of the MTF
            N = len(self.MTF_poisson)//2
            #Subtracts the square root of the DC term from the poisson MTF
            #to fond the noise floor of the Fourier transform
            poisson = self.MTF_poisson-np.sqrt(self.MTF_poisson.max())

            xx = poisson[N,N:,N]
            X_cut = np.where(xx<=0)[0][0]
            X_res = 1/(self.base_freq*X_cut/1e9)

            yy = poisson[N:,N,N]
            Y_cut = np.where(yy<=0)[0][0]
            Y_res = 1/(self.base_freq*Y_cut/1e9)

            zz = poisson[N,N,N:]
            Z_cut = np.where(zz<=0)[0][0]
            Z_res = 1/(self.base_freq*Z_cut/1e9)

            self.XYZ_res = np.array([X_res,Y_res,Z_res])

    def FWHM_measurement(self):
        """Script to find the FWHM of the PSF

        """
        PSF = self.PSF_readout - self.PSF_readout.min()

        #Define the axis lines of the PSF
        xx = PSF[self.camera.res//2,:,self.camera.res//2]
        yy = PSF[:,self.camera.res//2,self.camera.res//2]
        zz = PSF[self.camera.res//2,self.camera.res//2,:]

        guess = np.array((xx.max(), 0, self.camera.vox/self.mag))

        x = np.linspace(-self.FoV/2,self.FoV/2,self.camera.res)
        x_fit, _ = curve_fit(gaussian, x, xx, p0=guess)
        y_fit, _ = curve_fit(gaussian, x, yy, p0=guess)
        z_fit, _ = curve_fit(gaussian, x, zz, p0=guess)

        _, _, x_sigma = x_fit
        _, _, y_sigma = y_fit
        _, _, z_sigma = z_fit

        self.FWHM = np.array((x_sigma,y_sigma,z_sigma))*2.355*1e9

    def calculate_PSF(self,GUI=None):
        """Main function that creates the system PSF and MTF

        Parameters
        ----------
        GUI : Class
            PyQt5 GUI class

        """
        #Simplifies the res to shorten the lines
        res = self.camera.res

        #Traces the system and light-sheet
        self.field_tracing_reverse()
        self.light_sheet()

        #Defines the z-sampling in image space
        z_max = self.z_voxel_size*res/2
        z_val = np.linspace(-z_max,z_max,res)

        #See the tracing funtion for explanation of the same code block
        M = res//2
        x = y = np.linspace(-M,M,res)
        xx,yy = np.meshgrid(x,y)
        RR = np.sqrt(xx**2+yy**2)
        delta_k = self.k0*self.lenses[-1].NA/(res//2)
        k_xy = delta_k*RR
        k_z = np.sqrt(self.k0**2 - k_xy**2)
        k_z = np.nan_to_num(k_z)

        #Back aperture obliqueness of the last lens in the system
        bao = np.nan_to_num(1 / np.cos(self.lenses[-1].theta))

        #Generates a dipole ensamble using a Fibonacci lattice
        phi,theta = make_pol(self.ensamble)

        #Calculates average light-sheet polarization in image space
        l_p = np.array(((np.cos(self.alpha), 0, -np.sin(self.alpha)),
                        (0, 1, 0),
                        (np.sin(self.alpha), 0, np.cos(self.alpha))))@self.Ei_base

        #Empty arrays for storing total transmitted intensity, dipole
        #excitation coeficcient for all dipoles in ensamble, and the PSF
        tti = []
        ani = []
        self.PSF = np.zeros((res,res,res))
        #Iterates through the ensamble
        for i in range(len(phi)):
            #If the user uses the GUI, the load bar is directed to the GUI. If
            #the user simulates from the terminal, the loadbar is directed to
            #the terminal
            try:
                GUI.pbar.setValue(100*(i+1)//len(phi))
            except:
                loadbar(i,len(phi))

            #Current dipole polarization
            dip_th = theta[i]
            dip_ph = phi[i]
            pol = np.array((np.sin(dip_th)*np.cos(dip_ph),
                            np.sin(dip_th)*np.sin(dip_ph),
                            np.cos(dip_th)))

            #Excitation coeficcient is calculated
            if self.anisotropy == 0:
                Ae = 1
            elif self.anisotropy == 0.4:
                Ae = np.abs(pol@l_p)
            ani.append(collected_field(pol,np.nanmax(self.lenses[0].theta))*Ae)

            #Calculates the initial and final field
            Ei = E_0(pol, self.lenses[0].phi, self.lenses[0].theta,Ae)
            Ef = np.nan_to_num(self.apodization.reshape(res,res,1)*np.einsum('abji,abi->abj', self.transform, Ei))

            #Calculates the total transmitted field intensity
            initial_intensity = np.sum(np.abs(np.nan_to_num(Ei))**2)
            final_intensity = np.sum(np.abs(np.nan_to_num(Ef))**2)
            tti.append(final_intensity/initial_intensity)

            #Calculates the PSF based on the final field
            self.PSF += dft2_volume(Ef,k_z,z_val,bao,res,self.scaling)

        #Calculates the effective PSF
        self.eff_PSF = (self.PSF/self.PSF.max())*(self.ls_PSF/self.ls_PSF.max())

        #Calculates the optical eficciency of the system configuration
        throughput = np.array(ani)*np.array(tti)
        self.tti = np.mean(throughput)

        #Calculates the MTF and analyzes is
        self.make_MTF()
        self.save_stacks()
        self.analyze()
        self.FWHM_measurement()


################################################################################
#Example code for simulation without using GUI

def add_lenses(system):
    """Adds lenses to the system

    Parameters
    ----------
    system : Class
        Microscope class

    """
    NA_1 = 1.35
    RI_1 = 1.4
    system.add_lens(NA_1,RI_1)

    NA_2 = 0.25
    RI_2 = 1
    system.add_lens(NA_2,RI_2)

    NA_4 = 0.95
    RI_4 = 1
    rot_4 = 0
    system.add_lens(NA_4,RI_4,rot=rot_4)

    NA_3 = NA_2*(RI_1*NA_4)/(RI_4*NA_1)
    RI_3 = 1
    system.add_lens(NA_3,RI_3,pos=2)

    NA_5 = 1
    RI_5 = 1.7
    rot_5 = 40*np.pi/180
    system.add_lens(NA_5,RI_5,rot=rot_5)

    NA_6 = 1/40
    system.add_lens(NA_6)

def make_system(system):
    """Determines system configuration such as lens setup, camera configuration,
       dipoles in ensamble, light-sheet polarisation and opening, and SNR

    Parameters
    ----------
    system : Class
        Microscope class

    """
    add_lenses(system) #Adds lenses to the system
    system.add_camera(128,2e-6,100,1.4) #Defines camera config

    system.ensamble = 10 #Number of dipoles in ensamble
    system.OTF_res = 256 #Size of OTF in pixels
    system.ls_pol = 'u' #Ls polarization ['p', 's', or 'u']
    system.anisotropy = 0.4 #Anisotropy [0, or 0.4]
    system.ls_opening = 5*np.pi/180 #Ls opening half-angle in degrees
    system.SNR = 20 #Signal to noise ratio

    system.calculate_system_specs()

#Function to simulate pre-defined system
#made by the functions above
if __name__ == '__main__':
    ex = 488e-9 #Excitation wavelength
    em = 507e-9 #Emission wavelength
    path = 'test3'
    system = Microscope(ex,em,path) #Creates microscope
    make_system(system) #Determines the rest of the system configuration
    system.calculate_PSF() #Calculates the PSF of the system

    # import matplotlib.pyplot as plt
    # fig,ax = plt.subplots(2,2)
    # ax[0,0].imshow(system.PSF_poisson[:,:,system.camera.res//2])
    # ax[0,1].imshow(np.log(system.PSF_poisson[:,:,system.camera.res//2]+1))
    # ax[1,0].imshow(system.PSF_readout[:,:,system.camera.res//2])
    # ax[1,1].imshow(np.log(system.PSF_readout[:,:,system.camera.res//2]+1))
    # plt.show()
    #
    # fig,ax = plt.subplots(3,2)
    # ax[0,0].imshow(system.MTF_readout[:,:,system.OTF_res//2])
    # ax[0,1].imshow(np.log(system.MTF_readout[:,:,system.OTF_res//2]))
    # ax[1,0].imshow(system.MTF_background[:,:,system.OTF_res//2])
    # ax[1,1].imshow(np.log(system.MTF_background[:,:,system.OTF_res//2]))
    # ax[2,0].imshow(system.MTF_readout[:,:,system.OTF_res//2]-system.MTF_background[:,:,system.OTF_res//2])
    # ax[2,1].imshow(np.log(system.MTF_readout[:,:,system.OTF_res//2]-system.MTF_background[:,:,system.OTF_res//2]))
    # plt.show()

    
    import json
    
    path = 'test2'
    if not os.path.exists(path):
        os.mkdir(path)

    with tifffile.TiffWriter(path+'/PSF_poisson.tiff') as stack:
        stack.save(system.PSF_poisson.transpose(2,1,0),contiguous=True)
    with tifffile.TiffWriter(path+'/PSF_readout.tiff') as stack:
        stack.save(system.PSF_readout.transpose(2,1,0),contiguous=True)
    with tifffile.TiffWriter(path+'/MTF_poisson.tiff') as stack:
        stack.save(system.MTF_poisson.transpose(2,1,0),contiguous=True)
    with tifffile.TiffWriter(path+'/MTF_readout.tiff') as stack:
        stack.save(system.MTF_readout.transpose(2,1,0),contiguous=True)

    metadata = {'Dipoles in ensamble' : system.ensamble,
                'Emission wavelength [nm]' : np.round(system.lam_em*1e9,2),
                'Excitation wavelength [nm]' : np.round(system.lam_ex*1e9,2),
                'Full FoV [pixels]' : system.camera.res,
                'Full FoV in object space [microns]' : system.FoV*1e6,
                'Light sheet opening [degrees]' : np.round(system.ls_opening*180/np.pi),
                'Magnification transverse' : system.mag,
                'Magnification axial' : system.axial_mag,
                'MTF base frequency' : system.base_freq,
                'MTF size [pixels]' : system.OTF_res,
                'Optical efficiency' : system.tti,
                'Voxel size [microns]' : system.camera.vox*1e6}

    res_data = {'X_res [nm]' : system.XYZ_res[0],
                'Y_res [nm]' : system.XYZ_res[1],
                'Z_res [nm]' : system.XYZ_res[2],
                'X_FWHM [nm]' : system.FWHM[0]*1e9,
                'Y_FWHM [nm]' : system.FWHM[1]*1e9,
                'Z_FWHM [nm]' : system.FWHM[2]*1e9}

    with open(path+'/data.json', 'w') as output:
        json.dump(metadata|res_data, output, indent=4)
